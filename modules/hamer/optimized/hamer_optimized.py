import torch
import pytorch_lightning as pl
from pathlib import Path

from hamer.configs import CACHE_DIR_HAMER, get_config
from hamer.models import download_models
from hamer.models.backbones import create_backbone
from hamer.models.discriminator import Discriminator
from hamer.models.losses import Keypoint2DLoss, Keypoint3DLoss, ParameterLoss
from hamer.utils import SkeletonRenderer, MeshRenderer
from hamer.utils.geometry import aa_to_rotmat, perspective_projection

from .mano_wrapper_optimized import create_optimized_mano
from .mano_head_optimized import build_mano_head_optimized


class HamerOptimized(pl.LightningModule):

    def __init__(self, cfg, init_renderer=False):
        """
        Optimized HaMeR model with:
        - torch.compile() on MANO forward pass
        - AMP (bfloat16 autocast) in forward_step
        - Early stopping in IEF loop (via optimized MANO head)
        """
        super().__init__()
        self.cfg = cfg
        self.automatic_optimization = False

        # Create backbone
        self.backbone = create_backbone(cfg)
        if cfg.MODEL.BACKBONE.get('PRETRAINED_WEIGHTS', None):
            log = self.backbone.load_state_dict(torch.load(cfg.MODEL.BACKBONE.PRETRAINED_WEIGHTS, map_location='cpu')['state_dict'])

        # Create optimized MANO head (with early stopping)
        self.mano_head = build_mano_head_optimized(cfg)

        # Create optimized MANO model (with torch.compile)
        mano_cfg = {k.lower(): v for k, v in dict(cfg.MANO).items()}
        mano_cfg.pop('num_hand_joints', None)
        mano_cfg.pop('mean_params', None)
        mano_cfg.pop('create_body_pose', None)
        self.mano = create_optimized_mano(**mano_cfg)

        # Discriminator
        self.discriminator = Discriminator()

        # Losses
        self.keypoint_3d_loss = Keypoint3DLoss(loss_type='l1')
        self.keypoint_2d_loss = Keypoint2DLoss(loss_type='l1')
        self.mano_parameter_loss = ParameterLoss()

        # Renderers (optional)
        if init_renderer:
            self.skeleton_renderer = SkeletonRenderer(cfg)
            self.mesh_renderer = MeshRenderer(cfg, faces=self.mano.faces)
        else:
            self.skeleton_renderer = None
            self.mesh_renderer = None

    def forward_step(self, batch, train=False):
        """Forward pass with AMP autocast on CUDA, graceful CPU fallback."""

        x = batch['img']
        batch_size = x.shape[0]

        use_amp = x.is_cuda

        # Wrap in autocast for mixed precision on CUDA
        if use_amp:
            amp_ctx = torch.amp.autocast('cuda', dtype=torch.bfloat16)
        else:
            amp_ctx = torch.amp.autocast('cpu', enabled=False)

        with amp_ctx:
            # Backbone
            conditioning_feats = self.backbone(x[:, :, :, 32:-32])

            # MANO head with early stopping (eval only)
            pred_mano_params, pred_cam, pred_mano_params_list = self.mano_head(conditioning_feats)

        # Camera translation from weak perspective (keep in fp32)
        pred_cam_t = torch.stack([
            pred_cam[:, 1],
            pred_cam[:, 2],
            2 * self.cfg.EXTRA.FOCAL_LENGTH / (self.cfg.MODEL.IMAGE_SIZE * pred_cam[:, 0] + 1e-9)
        ], dim=-1)

        # MANO forward pass (compiled on GPU)
        mano_output = self.mano(**{k: v.float() for k, v in pred_mano_params.items()}, pose2rot=False)
        pred_keypoints_3d = mano_output.joints
        pred_vertices = mano_output.vertices

        # 2D projection
        focal_length = self.cfg.EXTRA.FOCAL_LENGTH * torch.ones(batch_size, 2, device=x.device, dtype=x.dtype)
        pred_keypoints_2d = perspective_projection(
            pred_keypoints_3d,
            translation=pred_cam_t,
            focal_length=focal_length / self.cfg.MODEL.IMAGE_SIZE,
        )

        output = {
            'pred_cam': pred_cam,
            'pred_cam_t': pred_cam_t,
            'pred_keypoints_3d': pred_keypoints_3d,
            'pred_keypoints_2d': pred_keypoints_2d,
            'pred_vertices': pred_vertices,
            'pred_mano_params': pred_mano_params,
        }

        # Lists for IEF intermediate predictions
        pred_mano_params_list_out = {}
        if pred_mano_params_list:
            num_iters = pred_mano_params_list['betas'].shape[0] // batch_size
            pred_cam_list = pred_mano_params_list['cam']
            pred_cam_t_list = torch.stack([
                pred_cam_list[:, 1],
                pred_cam_list[:, 2],
                2 * self.cfg.EXTRA.FOCAL_LENGTH / (self.cfg.MODEL.IMAGE_SIZE * pred_cam_list[:, 0] + 1e-9)
            ], dim=-1)

            mano_output_list = self.mano(
                **{k: v.float() for k, v in pred_mano_params_list.items() if k != 'cam'},
                pose2rot=False,
            )
            pred_keypoints_3d_list = mano_output_list.joints
            focal_length_list = self.cfg.EXTRA.FOCAL_LENGTH * torch.ones(num_iters * batch_size, 2, device=x.device, dtype=x.dtype)
            pred_keypoints_2d_list = perspective_projection(
                pred_keypoints_3d_list,
                translation=pred_cam_t_list,
                focal_length=focal_length_list / self.cfg.MODEL.IMAGE_SIZE,
            )
            output['pred_keypoints_3d_list'] = pred_keypoints_3d_list
            output['pred_keypoints_2d_list'] = pred_keypoints_2d_list

        return output

    def forward(self, batch):
        return self.forward_step(batch, train=False)

    def training_step(self, batch, batch_idx):
        opt, opt_d = self.optimizers()

        out = self.forward_step(batch, train=True)

        batch_size = batch['img'].shape[0]
        num_iters = out.get('pred_keypoints_3d_list', out['pred_keypoints_3d']).shape[0] // batch_size

        # 3D keypoint loss
        loss_keypoints_3d = self.keypoint_3d_loss(
            out['pred_keypoints_3d_list'] if 'pred_keypoints_3d_list' in out else out['pred_keypoints_3d'],
            batch['keypoints_3d'].unsqueeze(0).repeat(num_iters, 1, 1, 1).reshape(-1, *batch['keypoints_3d'].shape[1:]),
            pelvis_id=0,
            pelvis_align=True,
        )

        # 2D keypoint loss
        loss_keypoints_2d = self.keypoint_2d_loss(
            out['pred_keypoints_2d_list'] if 'pred_keypoints_2d_list' in out else out['pred_keypoints_2d'],
            batch['keypoints_2d'].unsqueeze(0).repeat(num_iters, 1, 1, 1).reshape(-1, *batch['keypoints_2d'].shape[1:]),
        )

        loss = self.cfg.LOSS_WEIGHTS.KEYPOINTS_3D * loss_keypoints_3d + \
               self.cfg.LOSS_WEIGHTS.KEYPOINTS_2D * loss_keypoints_2d

        # Generator step
        opt.zero_grad()
        self.manual_backward(loss)
        torch.nn.utils.clip_grad_norm_(
            [p for p in self.parameters() if p not in self.discriminator.parameters()],
            self.cfg.TRAIN.get('GRAD_CLIP', 1.0),
        )
        opt.step()

        self.log('train/loss', loss, prog_bar=True)
        return loss

    def configure_optimizers(self):
        param_groups = [{'params': filter(lambda p: p.requires_grad, self.parameters()), 'lr': self.cfg.TRAIN.LR}]
        optimizer = torch.optim.AdamW(params=param_groups, weight_decay=self.cfg.TRAIN.WEIGHT_DECAY)
        optimizer_disc = torch.optim.AdamW(
            params=self.discriminator.parameters(), lr=self.cfg.TRAIN.LR, weight_decay=self.cfg.TRAIN.WEIGHT_DECAY
        )
        return optimizer, optimizer_disc


def load_hamer_optimized(checkpoint_path, model_cfg=None):
    """Load an optimized HaMeR model from a standard checkpoint."""
    if model_cfg is None:
        model_cfg = get_config('_DEFAULT', update_cachedir=True)
    model = HamerOptimized(model_cfg)
    if checkpoint_path:
        checkpoint = torch.load(checkpoint_path, map_location='cpu')
        state_dict = checkpoint.get('state_dict', checkpoint)
        # Remap keys from base model to optimized model
        model.load_state_dict(state_dict, strict=False)
    return model, model_cfg
