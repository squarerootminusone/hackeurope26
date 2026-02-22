-- evaluations table schema
-- Tracks ML evaluation runs, their VMs, and container runtime timestamps.

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
