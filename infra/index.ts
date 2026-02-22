import * as pulumi from "@pulumi/pulumi";
import * as gcp from "@pulumi/gcp";

// =============================================================================
// Config
// =============================================================================

const config = new pulumi.Config();
const gcpConfig = new pulumi.Config("gcp");
const PROJECT = gcpConfig.require("project");
const REGION = gcpConfig.require("region");
const dbPassword = config.requireSecret("dbPassword");
const labels = { "bench-test": "true" };

// Short region tag for naming (e.g. "ew1" for europe-west1, "usc1" for us-central1)
const RTAG = REGION.replace("europe-west", "ew").replace("us-central", "usc").replace(/-/g, "");

// =============================================================================
// VPC (shared name — Pulumi will adopt the existing one per stack)
// =============================================================================

const network = new gcp.compute.Network("bench-test", {
  name: "bench-test",
  autoCreateSubnetworks: false,
  project: PROJECT,
});

const subnet = new gcp.compute.Subnetwork("bench-test-subnet", {
  name: `bench-test-${RTAG}`,
  network: network.id,
  ipCidrRange: REGION === "europe-west1" ? "10.0.0.0/20" : "10.2.0.0/20",
  region: REGION,
  project: PROJECT,
  secondaryIpRanges: [
    {
      rangeName: `gke-pods-${RTAG}`,
      ipCidrRange: REGION === "europe-west1" ? "10.4.0.0/14" : "10.16.0.0/14",
    },
    {
      rangeName: `gke-services-${RTAG}`,
      ipCidrRange: REGION === "europe-west1" ? "10.8.0.0/20" : "10.20.0.0/20",
    },
  ],
});

// =============================================================================
// GKE Autopilot
// =============================================================================

const cluster = new gcp.container.Cluster("bench-test-cluster", {
  name: `bench-test-cluster-${RTAG}`,
  location: REGION,
  project: PROJECT,
  enableAutopilot: true,
  network: network.id,
  subnetwork: subnet.id,
  ipAllocationPolicy: {
    clusterSecondaryRangeName: `gke-pods-${RTAG}`,
    servicesSecondaryRangeName: `gke-services-${RTAG}`,
  },
  releaseChannel: { channel: "REGULAR" },
  deletionProtection: false,
  resourceLabels: labels,
});

// =============================================================================
// Cloud SQL (MySQL 8.0)
// =============================================================================

const sqlInstance = new gcp.sql.DatabaseInstance("bench-test-eval-db", {
  name: `bench-test-eval-db-${RTAG}`,
  databaseVersion: "MYSQL_8_0",
  region: REGION,
  project: PROJECT,
  settings: {
    tier: "db-f1-micro",
    diskType: "PD_SSD",
    diskSize: 10,
    ipConfiguration: {
      ipv4Enabled: true,
      authorizedNetworks: [{ value: "0.0.0.0/0", name: "all" }],
    },
    userLabels: labels,
  },
  deletionProtection: false,
});

const database = new gcp.sql.Database("evaluations-db", {
  name: "evaluations_db",
  instance: sqlInstance.name,
  project: PROJECT,
});

const sqlUser = new gcp.sql.User("eval-user", {
  name: "eval_user",
  instance: sqlInstance.name,
  password: dbPassword,
  host: "%",
  project: PROJECT,
});

// =============================================================================
// GCS Bucket
// =============================================================================

const bucket = new gcp.storage.Bucket("bench-test-dependencies", {
  name: `bench-test-dependencies-${RTAG}-${PROJECT}`,
  location: REGION,
  project: PROJECT,
  uniformBucketLevelAccess: true,
  forceDestroy: true,
  labels: labels,
});

// =============================================================================
// Artifact Registry
// =============================================================================

const registry = new gcp.artifactregistry.Repository("bench-test-images", {
  repositoryId: "bench-test-images",
  format: "DOCKER",
  location: REGION,
  project: PROJECT,
  labels: labels,
});

// =============================================================================
// Secret Manager
// =============================================================================

const dbPasswordSecret = new gcp.secretmanager.Secret("eval-db-password", {
  secretId: `eval-db-password-${RTAG}`,
  project: PROJECT,
  replication: { auto: {} },
  labels: labels,
});

const dbPasswordVersion = new gcp.secretmanager.SecretVersion("eval-db-password-v1", {
  secret: dbPasswordSecret.id,
  secretData: dbPassword,
});

// =============================================================================
// Build VM
// =============================================================================

const buildVm = new gcp.compute.Instance("build-vm", {
  name: `build-vm-${RTAG}`,
  zone: `${REGION}-b`,
  machineType: "e2-standard-16",
  project: PROJECT,
  bootDisk: {
    initializeParams: {
      image: "debian-cloud/debian-12",
      size: 200,
      type: "pd-ssd",
    },
  },
  networkInterfaces: [
    {
      network: network.id,
      subnetwork: subnet.id,
      accessConfigs: [{}],
    },
  ],
  serviceAccount: {
    scopes: ["https://www.googleapis.com/auth/cloud-platform"],
  },
  labels: labels,
});

// =============================================================================
// Workload Identity — HaMeR
// =============================================================================

const hamerGsaName = `hamer-bench-${RTAG}`;
const hamerGsa = new gcp.serviceaccount.Account("hamer-bench-sa", {
  accountId: hamerGsaName,
  displayName: `HaMeR benchmark WI SA (${REGION})`,
  project: PROJECT,
});

new gcp.storage.BucketIAMMember("hamer-gsa-bucket-reader", {
  bucket: bucket.name,
  role: "roles/storage.objectViewer",
  member: pulumi.interpolate`serviceAccount:${hamerGsa.email}`,
});

new gcp.serviceaccount.IAMMember("hamer-wi-binding", {
  serviceAccountId: hamerGsa.name,
  role: "roles/iam.workloadIdentityUser",
  member: pulumi.interpolate`serviceAccount:${PROJECT}.svc.id.goog[default/${hamerGsaName}]`,
});

// =============================================================================
// Workload Identity — RAFT
// =============================================================================

const raftGsaName = `raft-bench-${RTAG}`;
const raftGsa = new gcp.serviceaccount.Account("raft-bench-sa", {
  accountId: raftGsaName,
  displayName: `RAFT benchmark WI SA (${REGION})`,
  project: PROJECT,
});

new gcp.storage.BucketIAMMember("raft-gsa-bucket-reader", {
  bucket: bucket.name,
  role: "roles/storage.objectViewer",
  member: pulumi.interpolate`serviceAccount:${raftGsa.email}`,
});

new gcp.storage.BucketIAMMember("raft-gsa-bucket-writer", {
  bucket: bucket.name,
  role: "roles/storage.objectCreator",
  member: pulumi.interpolate`serviceAccount:${raftGsa.email}`,
});

new gcp.serviceaccount.IAMMember("raft-wi-binding", {
  serviceAccountId: raftGsa.name,
  role: "roles/iam.workloadIdentityUser",
  member: pulumi.interpolate`serviceAccount:${PROJECT}.svc.id.goog[default/${raftGsaName}]`,
});

// =============================================================================
// Outputs
// =============================================================================

export const vpcName = network.name;
export const subnetName = subnet.name;
export const clusterName = cluster.name;
export const clusterEndpoint = cluster.endpoint;
export const sqlInstanceIp = sqlInstance.publicIpAddress;
export const sqlConnectionName = sqlInstance.connectionName;
export const dbName = database.name;
export const bucketName = bucket.name;
export const bucketUrl = bucket.url;
export const registryUrl = pulumi.interpolate`${REGION}-docker.pkg.dev/${PROJECT}/bench-test-images`;
