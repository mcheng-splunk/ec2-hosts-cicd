# OpenTelemetry Ansible Setup for EC2 Hosts

This project deploys OpenTelemetry Collector (otelcol-contrib) across EC2 infrastructure using Ansible. It supports a scalable architecture with HAProxy load balancing for high availability.

## Architecture Overview

### High-Level Structure

```
ec2-hosts/
├── site.yml                  # Main entry point (imports all playbooks)
├── webservers.yml           # Playbook for webserver tier
├── db_machines.yml          # Playbook for database tier
├── otel_collector.yml       # Playbook for OTel collector tier
├── otel_lb.yml              # Playbook for HAProxy load balancer
├── teardown_all.yml         # Playbook to remove all OTel components
├── teardown_agents.yml      # Playbook to remove OTel Agent (webservers, db_machines)
├── teardown_collectors.yml  # Playbook to remove OTel Collector
├── teardown_lb.yml          # Playbook to remove HAProxy Load Balancer
├── ansible.cfg              # Ansible configuration (includes OTel callback plugin)
├── callback_plugins/        # Custom Ansible callback plugins
│   └── opentelemetry_tracer.py
├── .github/
│   └── workflows/           # CI/CD GitHub Actions workflows
│       └── ansible-gitops.yml
├── inventories/             # Environment-specific configurations
│   ├── dev/                 # Development environment
│   │   ├── hosts.ini        # Host inventory with connection details
│   │   └── group_vars/      # Variables per host group
│   │       ├── all.yaml     # Environment-wide variables
│   │       ├── webservers/
│   │       │   └── vars.yaml
│   │       ├── db_machines/
│   │       │   ├── vars.yaml
│   │       │   └── vault.yaml   # Encrypted secrets (Vault)
│   │       ├── otel_collector/
│   │       │   └── vars.yaml
│   │       └── haproxy_otel_lb/
│   │           └── vars.yaml
│   └── test/                # Test environment (same structure)
└── roles/
    ├── otel_agent/          # Role for OTel Agent deployment
    │   ├── tasks/main.yml
    │   ├── handlers/main.yml
    │   ├── vars/main.yml
    │   └── templates/
    │       └── config.yaml.j2
    ├── otel_collector/      # Role for OTel Collector deployment
    │   ├── tasks/main.yml
    │   ├── handlers/main.yml
    │   ├── vars/main.yml
    │   └── templates/
    │       └── config.yaml.j2
    └── haproxy_otel_lb/     # Role for HAProxy load balancer
        ├── tasks/main.yml
        ├── handlers/main.yml
        └── templates/
            └── haproxy.cfg.j2
```

### OpenTelemetry Architecture

The project implements a **3-tier architecture** with load balancing:

```
OTel Agent (webservers, db_machines)
    ↓ (forwards to haproxy_endpoint:4317)
    
HAProxy (haproxy_otel_lb)
    ↓ (load balances across otel_collectors on 4317)
    
OTel Collector (otel_collector)
    ↓ (exports to otel_backend_endpoint:4317)
    
Final Backend (Jaeger, etc.)
```

**Traffic Flow:**
1. **OTel Agent** runs on webservers and db_machines, collecting host metrics and database-specific metrics
2. Agents forward OTLP data to **HAProxy** on port 4317
3. **HAProxy** load balances across multiple OTel Collectors using roundrobin
4. **OTel Collectors** receive data and forward to a final backend (e.g., Jaeger)

### OpenTelemetry Instrumentation

The project includes an Ansible callback plugin (`callback_plugins/opentelemetry_tracer.py`) that automatically captures OpenTelemetry traces for all playbook executions:

**What gets traced:**
- Playbook start/end (root span)
- Each play start/end
- Each task execution per host (ok, failed, skipped, unreachable)
- Playbook statistics (success/failure counts per host)

**To enable tracing:**
1. Install dependencies: `pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc`
2. Set `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable (e.g., `http://localhost:4317`)
3. Run any playbook normally - the callback plugin is enabled in `ansible.cfg`

See [oneuptime.com](https://oneuptime.com/blog/post/2026-02-06-instrument-ansible-playbook-opentelemetry/) for more details on the callback plugin implementation.

### Host Tiers

| Tier | Description | Example Hosts |
|------|-------------|---------------|
| `webservers` | Web application servers running OTel Agent | dev-web-01, dev-web-02 |
| `db_machines` | Database servers running OTel Agent | dev-db-01 |
| `otel_collector` | OTel Collector instances behind HAProxy | dev-collector-01, dev-collector-02 |
| `haproxy_otel_lb` | HAProxy load balancer | haproxy01 |

### Supported Databases

The OpenTelemetry Agent configuration is dynamically generated based on `db_type`:

| Database Type | Port | Receiver | Config |
|------|------|----------|--------|
| PostgreSQL | 5432 | `postgresql` | TLS insecure mode enabled |
| MySQL | 3306 | `mysql` | Standard connection |
| MSSQL | 1433 | `sqlserver` | SQL Server connection string |
| Oracle | 1521 | `oracledb` | XEPDB1 container |

### OpenTelemetry Agent Configuration

**Receivers:**
- `otlp` (gRPC/HTTP) - For incoming OTLP traces/metrics from applications
- `hostmetrics` - System metrics (CPU, memory, load)
- Database-specific receiver (based on `db_type`)

**Processors:**
- `batch` - Batches samples for efficient export
- `memory_limiter` - Prevents memory exhaustion (512 MiB limit)

**Exporters:**
- `otlp` - Forwards to HAProxy (configured via `haproxy_endpoint`)
- `debug` - Outputs to console (detailed verbosity)

### OpenTelemetry Collector Configuration

**Receivers:**
- `otlp` (gRPC port 4317, HTTP port 4318)
- Health check extension on port 13133 (for HAProxy health checks)

**Processors:**
- `batch` - Batches samples for efficient export

**Exporters:**
- `otlp` - Forwards to final backend (configured via `otel_backend_endpoint`)
- `debug` - Outputs to console (detailed verbosity)

### HAProxy Configuration

**Frontend:**
- Listens on port 4317 (OTLP gRPC)
- Routes to `otel_collectors` backend

**Backend:**
- Load balancing: `roundrobin`
- Health checks: HTTP GET on `/` to port 13133
- Dynamic server list based on inventory group

## Folder Structure Details

### Inventories (`inventories/`)

Each environment (dev, test, prod) has its own inventory directory with:

**hosts.ini** - Host definitions with connection parameters:
```ini
[webservers]
webserver-name ansible_host=IP ansible_user=user ansible_ssh_private_key_file=key-path

[db_machines]
dbserver-name ansible_host=IP ansible_user=user db_type=postgres otel_metrics_port=port ansible_ssh_private_key_file=key-path

[otel_collector]
collector-name ansible_host=IP ansible_user=user otel_metrics_port=port ansible_ssh_private_key_file=key-path

[haproxy_otel_lb]
haproxy-name ansible_host=IP ansible_user=user ansible_ssh_private_key_file=key-path
```

**group_vars/** - Variables organized by host group:
- `all.yaml` - Environment-wide variables (e.g., `env_stage: dev`, `haproxy_endpoint`)
- `webservers/vars.yaml` - Web tier overrides
- `db_machines/vars.yaml` - DB tier overrides
- `db_machines/vault.yaml` - **Encrypted** secrets (database credentials)
- `otel_collector/vars.yaml` - OTel Collector tier overrides
- `haproxy_otel_lb/vars.yaml` - HAProxy-specific variables

### Roles (`roles/`)

#### otel_agent
Deploys OTel Agent on webservers and db_machines:
1. Installs Python prerequisites
2. Downloads OTel Contrib package
3. Installs package (apt/dnf based on OS)
4. Deploys config template with database-specific receivers
5. Starts/enables otelcol-contrib service

#### otel_collector
Deploys OTel Collector on otel_collector:
1. Installs Python prerequisites
2. Downloads OTel Contrib package
3. Installs package (apt/dnf based on OS)
4. Deploys config template with OTLP receiver and health_check extension
5. Starts/enables otelcol-contrib service

#### haproxy_otel_lb
Deploys HAProxy on haproxy_otel_lb:
1. Installs HAProxy package
2. Deploys configuration from Jinja2 template
3. Starts/enables HAProxy service

## Deployment Scenarios

### Prerequisites

1. **SSH Access:**
   - Private key file path configured in `hosts.ini`
   - SSH key has access to all target hosts
   - User (typically `ec2-user` or `ubuntu`) has sudo privileges

2. **Vault Setup (for database secrets):**
   - Create `.vault_pass.txt` with the Vault decryption password
   - Ensure `vault_db_password` is set in `group_vars/db_machines/vault.yaml`
   - **Note:** When running from CLI, use `--ask-vault-pass` to enter the password interactively. When running from CI/CD (GitHub Actions), use `--vault-password-file` with the secret stored in repository secrets.

3. **OpenTelemetry Package:**
   - Supported: RPM (RHEL/Amazon Linux) or DEB (Debian/Ubuntu)
   - Set `otel_pkg_type` in `group_vars/*/vars.yaml`

### Deploy to All Hosts in Dev Environment

```bash
# Using site.yml (deploys all tiers: webservers, db_machines, otel_collectors, haproxy)
ansible-playbook -i inventories/dev/ site.yml --ask-vault-pass

# Or run individually by tier
ansible-playbook -i inventories/dev/ webservers.yml --ask-vault-pass
ansible-playbook -i inventories/dev/ db_machines.yml --ask-vault-pass
ansible-playbook -i inventories/dev/ otel_collector.yml --ask-vault-pass
ansible-playbook -i inventories/dev/ otel_lb.yml --ask-vault-pass
```

### Deploy Only to Webservers

```bash
# For dev environment
ansible-playbook -i inventories/dev/ webservers.yml --ask-vault-pass

# For test environment
ansible-playbook -i inventories/test/ webservers.yml --ask-vault-pass
```

### Deploy Only to DB Servers

```bash
# Deploy to all dbservers in dev (all DB types supported)
ansible-playbook -i inventories/dev/ db_machines.yml --ask-vault-pass

# Deploy to specific DB server (using --limit)
ansible-playbook -i inventories/dev/ db_machines.yml --limit dev-db-01 --ask-vault-pass
```

### Deploy Only to OTel Collectors

```bash
# Deploy to all otel_collectors
ansible-playbook -i inventories/dev/ otel_collector.yml --ask-vault-pass

# Deploy to specific collector
ansible-playbook -i inventories/dev/ otel_collector.yml --limit dev-collector-01 --ask-vault-pass
```

### Deploy Only HAProxy

```bash
# Deploy HAProxy load balancer
ansible-playbook -i inventories/dev/ otel_lb.yml --ask-vault-pass
```

### Deploy to Different Environments

```bash
# Test environment
ansible-playbook -i inventories/test/ site.yml --ask-vault-pass

# Production environment (requires prod/ inventory directory)
ansible-playbook -i inventories/prod/ site.yml --ask-vault-pass
```

### Dry Run (Check Mode)

```bash
# Preview changes without applying
ansible-playbook -i inventories/dev/ site.yml --check --ask-vault-pass

# Check specific tier
ansible-playbook -i inventories/dev/ otel_lb.yml --check --ask-vault-pass
```

### Destroy/Teardown OpenTelemetry Infrastructure

```bash
# Remove all OTel components from all hosts in dev
ansible-playbook -i inventories/dev/ teardown_all.yml --ask-vault-pass

# Remove from specific tier
ansible-playbook -i inventories/dev/ teardown_agents.yml --ask-vault-pass
ansible-playbook -i inventories/dev/ teardown_collectors.yml --ask-vault-pass
ansible-playbook -i inventories/dev/ teardown_lb.yml --ask-vault-pass

# Remove from specific tier in dev (using --limit)
ansible-playbook -i inventories/dev/ teardown_agents.yml --limit webservers --ask-vault-pass
ansible-playbook -i inventories/dev/ teardown_agents.yml --limit db_machines --ask-vault-pass
ansible-playbook -i inventories/dev/ teardown_collectors.yml --limit otel_collector --ask-vault-pass
ansible-playbook -i inventories/dev/ teardown_lb.yml --limit haproxy_otel_lb --ask-vault-pass

# Remove from specific host (by name)
ansible-playbook -i inventories/dev/ teardown_agents.yml --limit dev-web-01 --ask-vault-pass
ansible-playbook -i inventories/dev/ teardown_collectors.yml --limit dev-collector-01 --ask-vault-pass

# Check mode - see what would be removed
ansible-playbook -i inventories/dev/ teardown_all.yml --check --ask-vault-pass
```

**Teardown playbooks:**
- `teardown_all.yml` - Removes all OTel components (imports all other teardown playbooks)
- `teardown_agents.yml` - Stops/removes otelcol-contrib from webservers and db_machines
- `teardown_collectors.yml` - Stops/removes otelcol-contrib from otel_collector hosts
- `teardown_lb.yml` - Stops/removes HAProxy from haproxy_otel_lb hosts

### Deploy with Verbose Output

```bash
# Verbose logging
ansible-playbook -i inventories/dev/ site.yml -v --ask-vault-pass

# Very verbose (debug)
ansible-playbook -i inventories/dev/ site.yml -vvvv --ask-vault-pass
```

## Variable Reference

### Default Variables (role/vars/main.yml)

| Variable | Description | Default |
|------|------|------|
| `otel_version` | OpenTelemetry Collector version | `0.152.0` |
| `otel_arch` | Architecture (auto-detected) | `amd64` |
| `otel_config_dir` | Configuration directory | `/etc/otelcol-contrib` |
| `otel_config_file` | Config filename | `config.yaml` |
| `otel_download_url` | Download URL (auto-generated) | GitHub Releases |
| `otel_pkg_type` | Package type (rpm/deb) | Auto-detected based on OS |

### Environment Variables (group_vars/all.yaml)

| Variable | Description | Example |
|------|------|------|
| `env_stage` | Environment name | `dev`, `test`, `prod` |
| `haproxy_endpoint` | HAProxy IP:port for otel_agent | `{{ hostvars[groups['otel_lb'][0]]['ansible_host'] }}:4317` |

### Host Variables (group_vars/*/vars.yaml)

| Variable | Description | Example |
|------|------|------|
| `otel_pkg_type` | Package type (rpm/deb) | `rpm` for Amazon Linux |
| `otel_metrics_port` | Application metrics port | `5432` |
| `haproxy_endpoint` | HAProxy endpoint (for webservers/db_machines) | IP:4317 |
| `otel_backend_endpoint` | Final backend for otel_collector | `jaeger-collector.svc:4317` |

### HAProxy Variables (group_vars/otel_lb/vars.yaml)

| Variable | Description | Example |
|------|------|------|
| `otel_lb_endpoint` | HAProxy IP:port | `{{ ansible_host }}:4317` |
| `otel_collector_backend_endpoint` | OTel Collector backend endpoint | IP:4317 |
| `otel_backend_endpoint` | Final backend for otel_collectors | IP:4317 |

### Host Inventory Variables (hosts.ini)

| Variable | Description | Required |
|------|------|------|
| `ansible_host` | IP address or hostname | Yes |
| `ansible_user` | SSH username | Yes |
| `ansible_ssh_private_key_file` | Path to private key | Yes |
| `db_type` | Database type (for db_machines) | Yes (for db_machines) |
| `otel_metrics_port` | Database metrics port | Yes (for db_machines) |

### Vault Variables (group_vars/db_machines/vault.yaml)

| Variable | Description | Encrypted |
|------|------|------|
| `vault_db_password` | Database monitor password | Yes |

## OTel Agent and Collector Configuration

### OTel Agent Configuration (webservers, db_machines)

The agent receives on:
- gRPC: port 4317
- HTTP: port 4318

It exports to HAProxy (configured via `haproxy_endpoint`).

**Database receivers are dynamically enabled based on `db_type`:**
- `postgresql` - for PostgreSQL databases
- `mysql` - for MySQL databases
- `sqlserver` - for MSSQL databases
- `oracledb` - for Oracle databases

### OTel Collector Configuration (otel_collectors)

Collectors receive on:
- gRPC: port 4317
- HTTP: port 4318

Health check endpoint: port 13133 (used by HAProxy)

It exports to the final backend (configured via `otel_backend_endpoint`).

### HAProxy Configuration (otel_lb)

HAProxy listens on port 4317 and load balances to OTel Collectors.

**Backend servers are dynamically discovered from the inventory `otel_collectors` group.**

## CI/CD Deployment (GitHub Actions)

The `.github/workflows/ansible-gitops.yml` workflow enables:

1. **Automatic deployment on git push** to main branch when files change
2. **Manual deployment** via workflow_dispatch with environment/tier selection

### Triggering Manual Deployment

1. Go to GitHub > Actions > "Ansible GitOps"
2. Click "Run workflow" dropdown
3. Select:
   - **Target Environment**: dev, test, or prod
   - **Target Tier**: otel_lb, otel_collectors, db_machines, webservers, or ALL

### Required Secrets

Add these to your GitHub repository settings:

| Secret | Description |
|------|------|
| `SSH_PRIVATE_KEY` | Private SSH key for host access |
| `ANSIBLE_VAULT_PASSWORD` | Password for decrypting vault.yaml |

## Adding a New Environment

1. Create inventory directory: `mkdir -p inventories/prod/group_vars/{webservers,db_machines,otel_collectors,otel_lb}`
2. Copy and modify templates:
   - `inventories/dev/hosts.ini` → `inventories/prod/hosts.ini` (update IPs)
   - `inventories/dev/group_vars/all.yaml` → `inventories/prod/group_vars/all.yaml` (change `env_stage`)
3. Create vault file with secrets: `ansible-vault create inventories/prod/group_vars/db_machines/vault.yaml`
4. Test deployment with `--check` mode first

## Adding a New Database Type

1. Update `roles/otel_agent/templates/config.yaml.j2`:
   - Add new receiver block in the `receivers:` section
   - Add receiver name to the pipeline `receivers:` list
2. Update host inventory with `db_type=newdbtype`
3. Add database credentials to vault file

## Scaling the Architecture

### Adding More OTel Collectors

1. Add new hosts to `otel_collectors` group in `hosts.ini`
2. Run: `ansible-playbook -i inventories/dev/ otel_collector.yml --ask-vault-pass`

### Adding More HAProxy Instances (High Availability)

1. Add additional HAProxy hosts to `otel_lb` group
2. Update DNS or load balancer to distribute traffic across HAProxy instances

## Troubleshooting

### Common Issues

1. **SSH Connection Failed:**
   ```bash
   # Verify SSH key and connectivity
   ssh -i ~/.ssh/aws-id-rsa.pem ec2-user@HOST_IP
   ```

2. **Vault Decryption Error:**
   ```bash
   # Verify vault password file exists and is readable
   cat .vault_pass.txt
   # Verify vault file is actually encrypted
   head inventories/dev/group_vars/db_machines/vault.yaml
   ```

3. **Package Installation Failed:**
   - Verify `otel_pkg_type` matches host OS (rpm vs deb)
   - Check network connectivity to GitHub Releases

4. **Collector Not Starting:**
   ```bash
   # SSH to host and check logs
   ssh -i ~/.ssh/aws-id-rsa.pem ec2-user@HOST_IP
   sudo journalctl -u otelcol-contrib -f
   ```

5. **HAProxy Not Routing Traffic:**
   ```bash
   # Check HAProxy status
   ssh -i ~/.ssh/aws-id-rsa.pem ec2-user@HAProxy_IP
   sudo systemctl status haproxy
   sudo haproxy -c -f /etc/haproxy/haproxy.cfg
   ```

### Verifying Deployment

```bash
# Check if OTel agent is running on webservers
ansible -i inventories/dev/ webservers -m service -a "name=otelcol-contrib state=started" --ask-vault-pass

# Check if OTel collector is running on collectors
ansible -i inventories/dev/ otel_collectors -m service -a "name=otelcol-contrib state=started" --ask-vault-pass

# Check if HAProxy is running
ansible -i inventories/dev/ otel_lb -m service -a "name=haproxy state=started" --ask-vault-pass

# View logs from all hosts
ansible -i inventories/dev/ all -m shell -a "sudo journalctl -u otelcol-contrib -n 20" --ask-vault-pass

# View HAProxy logs
ansible -i inventories/dev/ otel_lb -m shell -a "sudo journalctl -u haproxy -n 20" --ask-vault-pass
```

### Testing the Full Pipeline

1. **Test HAProxy connectivity:**
   ```bash
   # From a client machine, send OTLP data to HAProxy
   curl -X POST http://<haproxy-ip>:4317/v1/metrics \
     -H "Content-Type: application/json" \
     -d @test-data.json
   ```

2. **Verify OTel Collector receives data:**
   ```bash
   # Check collector logs for incoming data
   ansible -i inventories/dev/ otel_collectors -m shell -a "sudo journalctl -u otelcol-contrib -f"
   ```

3. **Verify final backend receives data:**
   - Check Jaeger UI or your backend's monitoring interface
