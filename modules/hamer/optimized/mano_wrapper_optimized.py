import torch
import numpy as np
import pickle
from typing import Optional
import smplx
from smplx.lbs import vertices2joints
from smplx.utils import MANOOutput, to_tensor
from smplx.vertex_ids import vertex_ids


class MANOOptimized(smplx.MANOLayer):
    def __init__(self, *args, joint_regressor_extra: Optional[str] = None, **kwargs):
        """
        Extension of the official MANO implementation to support more joints,
        with optional torch.compile() optimization for GPU inference.
        Args:
            Same as MANOLayer.
            joint_regressor_extra (str): Path to extra joint regressor.
        """
        super(MANOOptimized, self).__init__(*args, **kwargs)
        mano_to_openpose = [0, 13, 14, 15, 16, 1, 2, 3, 17, 4, 5, 6, 18, 10, 11, 12, 19, 7, 8, 9, 20]

        if joint_regressor_extra is not None:
            self.register_buffer('joint_regressor_extra', torch.tensor(pickle.load(open(joint_regressor_extra, 'rb'), encoding='latin1'), dtype=torch.float32))
        self.register_buffer('extra_joints_idxs', to_tensor(list(vertex_ids['mano'].values()), dtype=torch.long))
        self.register_buffer('joint_map', torch.tensor(mano_to_openpose, dtype=torch.long))

    def compile(self):
        """Wrap forward with torch.compile() for GPU acceleration.
        Skips compilation on CPU (not supported / no benefit).
        """
        if torch.cuda.is_available():
            self.forward = torch.compile(
                self.forward,
                mode="default",
                fullgraph=False,
                dynamic=True,
            )
        return self

    def forward(self, *args, **kwargs) -> MANOOutput:
        """
        Run forward pass. Same as MANO and also append an extra set of joints if joint_regressor_extra is specified.
        """
        mano_output = super(MANOOptimized, self).forward(*args, **kwargs)
        extra_joints = torch.index_select(mano_output.vertices, 1, self.extra_joints_idxs)
        joints = torch.cat([mano_output.joints, extra_joints], dim=1)
        joints = joints[:, self.joint_map, :]
        if hasattr(self, 'joint_regressor_extra'):
            extra_joints = vertices2joints(self.joint_regressor_extra, mano_output.vertices)
            joints = torch.cat([joints, extra_joints], dim=1)
        mano_output.joints = joints
        return mano_output


def create_optimized_mano(**kwargs):
    """Factory function that creates a MANOOptimized instance.
    Note: torch.compile() is disabled on MANO because SMPLX uses default
    parameters with batch=1 that are incompatible with dynamo tracing."""
    mano = MANOOptimized(**kwargs)
    # Skip compile — SMPLX default params (global_orient batch=1) break dynamo
    return mano
