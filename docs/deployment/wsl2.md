# Windows (WSL 2 + Docker Desktop)

MoE Sovereign runs on Windows via **WSL 2** (Windows Subsystem for Linux 2)
with Docker Desktop. This is the recommended path for Windows development and
local testing — the full compose stack including GPU pass-through works out of
the box once WSL 2 is configured correctly.

## Recommended WSL 2 resource limits

WSL 2 uses a lightweight Hyper-V VM that shares memory with Windows.
Without explicit limits it may consume all available RAM.
Create or edit `%USERPROFILE%\.wslconfig`:

```ini
# %USERPROFILE%\.wslconfig
# Settings apply to all WSL 2 distros on this machine.

[wsl2]
# Limit the VM to a fixed amount of RAM (adjust to your hardware)
memory=16GB

# Number of virtual processors available to WSL 2
processors=12

# Swap file — keep small, MoE Sovereign data lives in volumes not swap
swap=2GB
swapfile=C:\\Windows\\Temp\\wsl-swap.vhdx

# Allow localhost forwarding (required for browser access to services)
localhostforwarding=true
```

Apply changes by restarting WSL:

```powershell
wsl --shutdown
```

## Installing Docker Desktop

1. Download **Docker Desktop ≥ 4.30** from
   <https://docs.docker.com/desktop/install/windows-install/>.
2. During setup enable **"Use WSL 2 based engine"**.
3. After installation: Settings → Resources → WSL Integration →
   enable your distro (Ubuntu recommended).

## Cloning and running install.sh

Open your WSL terminal (e.g. Ubuntu) and run:

```bash
sudo apt-get update && sudo apt-get install -y git curl openssl
git clone https://github.com/moe-sovereign/moe-sovereign.git /opt/moe-sovereign
cd /opt/moe-sovereign
sudo bash install.sh
```

`install.sh` detects that no public domain is set and writes a minimal
`Caddyfile` stub — Caddy starts without TLS errors and the rest of the
stack comes up normally.

## Common error: Caddyfile mount failure

```text
Error response from daemon: failed to create task for container:
... mount src=/opt/moe-sovereign/Caddyfile ...
not a directory: Are you trying to mount a directory onto a file?
```

**Cause:** `Caddyfile` does not exist on the host. Docker creates a
*directory* at the mount path instead of waiting for a file, which then
fails at container start.

**Fix (if install.sh was already run without generating it):**

```bash
echo ':80 { respond "MoE Sovereign" 200 }' \
  > /opt/moe-sovereign/Caddyfile
sudo docker compose up -d moe-caddy
```

`install.sh` now generates this stub automatically when no domain is
configured — the error should not appear on fresh installs.

## Port access from Windows browser

With `localhostforwarding=true` in `.wslconfig`, services are accessible
directly from the Windows browser:

| Service | URL |
|---------|-----|
| Admin UI | <http://localhost:8080> |
| User Portal | <http://localhost:8088> |
| API | <http://localhost:8002> |
| Grafana | <http://localhost:3000> |
| Log Viewer | <http://localhost:9080> (Dozzle) |

## GPU pass-through (NVIDIA)

WSL 2 supports CUDA via the **NVIDIA CUDA on WSL** driver. No separate
guest driver is needed — install the Windows NVIDIA driver ≥ 525 and
enable GPU in Docker Desktop:

```bash
# Verify CUDA is visible inside WSL
nvidia-smi
```

Then set GPU resources in the MoE Admin UI as usual under
**Configuration → Inference Servers**.

## Known limitations on WSL 2

| Limitation | Workaround |
|------------|-----------|
| `/opt` not bind-mountable by default on older Docker Desktop | Upgrade to Docker Desktop ≥ 4.30, or change `MOE_DATA_ROOT` to `~/moe-data` |
| Neo4j memory maps (`mmap`) can exhaust WSL VM memory | Add `NEO4J_server_memory_pagecache_size=512m` to `.env` |
| Filesystem performance slower than native Linux | Store volumes on the ext4 WSL filesystem (`/home/...`), not on Windows NTFS mounts (`/mnt/c/...`) |
