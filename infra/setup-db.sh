#!/bin/bash
set -euo pipefail

# ---------------------------------------------------------------------------
# setup-db.sh — One-time Cloud SQL MySQL instance + evaluations table setup
# ---------------------------------------------------------------------------

INSTANCE_NAME="bench-test-eval-db"
REGION="europe-west1"
TIER="db-f1-micro"
DB_NAME="evaluations_db"
DB_USER="eval_user"
PROJECT=$(gcloud config get-value project)

# Use DB_PASS env var, or generate a random one
if [[ -z "${DB_PASS:-}" ]]; then
  DB_PASS=$(openssl rand -base64 18)
  echo "Generated DB password: $DB_PASS"
fi

echo "Project:  $PROJECT"
echo "Instance: $INSTANCE_NAME"
echo "Region:   $REGION"
echo "Tier:     $TIER"
echo ""

# 1. Create Cloud SQL instance
echo "==> Creating Cloud SQL instance (this takes a few minutes)..."
gcloud sql instances create "$INSTANCE_NAME" \
  --database-version=MYSQL_8_0 \
  --tier="$TIER" \
  --region="$REGION" \
  --storage-type=SSD \
  --storage-size=10GB \
  --assign-ip \
  --root-password="$DB_PASS" \
  --project="$PROJECT"

# 2. Authorize caller's IP so we can run DDL
MY_IP=$(curl -s https://ifconfig.me)
echo "==> Authorizing local IP $MY_IP on Cloud SQL..."
gcloud sql instances patch "$INSTANCE_NAME" \
  --authorized-networks="$MY_IP/32" \
  --project="$PROJECT" \
  --quiet

# 3. Get the Cloud SQL public IP
DB_HOST=$(gcloud sql instances describe "$INSTANCE_NAME" \
  --project="$PROJECT" \
  --format="value(ipAddresses[0].ipAddress)")
echo "Cloud SQL IP: $DB_HOST"

# 4. Create database
echo "==> Creating database $DB_NAME..."
gcloud sql databases create "$DB_NAME" \
  --instance="$INSTANCE_NAME" \
  --project="$PROJECT"

# 5. Create application user
echo "==> Creating user $DB_USER..."
gcloud sql users create "$DB_USER" \
  --instance="$INSTANCE_NAME" \
  --password="$DB_PASS" \
  --host="%" \
  --project="$PROJECT"

# 6. Create evaluations table
echo "==> Creating evaluations table..."
mysql -h "$DB_HOST" -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" <<'SQL'
CREATE TABLE IF NOT EXISTS evaluations (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    evaluation_name    VARCHAR(255) NOT NULL,
    model_name         VARCHAR(255) NOT NULL,
    is_optimized       BOOLEAN NOT NULL DEFAULT FALSE,
    vm_reference       VARCHAR(255) NOT NULL,
    instance_type      VARCHAR(255) NOT NULL,
    create_date        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_date        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    start_runtime_date TIMESTAMP NULL DEFAULT NULL,
    end_runtime_date   TIMESTAMP NULL DEFAULT NULL
);
SQL

echo ""
echo "============================================"
echo "  Setup complete"
echo "============================================"
echo "  Instance:  $INSTANCE_NAME"
echo "  Database:  $DB_NAME"
echo "  User:      $DB_USER"
echo "  Host:      $DB_HOST"
echo ""
echo "Export these for run-eval.sh:"
echo "  export DB_HOST=$DB_HOST"
echo "  export DB_USER=$DB_USER"
echo "  export DB_PASS=<your password>"
echo "  export DB_NAME=$DB_NAME"
echo "  export SQL_INSTANCE=$INSTANCE_NAME"
echo "============================================"
