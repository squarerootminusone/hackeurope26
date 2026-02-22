# Workflow

1. In the dashboard input the repo and SCI parameters
2. Upon clicking "Evaluation" the backend process starts
  - optimized version is created
  - both the original and optimized versions are contenerized and pushed to Artifact Registry
  - the VMs that run the evaluations are spawned alongside the evaluation database entry
3. When a VM finishes it updates the database entry with its runtime
4. The dashboard pulls information about finished jobs and shows the SCI

# Database
To connect to the MySQL instance see `src/db.py`. The schema is available in `infra/schema.sql`. 