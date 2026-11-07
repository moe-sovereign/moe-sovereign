#!/bin/bash
# Full MoE Sovereign research stack on one LUMI-G node (small-g), followed by a rollout driver run.
#
#   sbatch --time=02:00:00 --export=ALL,TASKS=tasks/orch_tasks.jsonl,N=20,FORKS=2 lumig_moe_stack.slurm
#
# Layout under $R (= $SCRATCH/moe-stack):
#   sif/      container images (orchestrator, mcp, embed, postgres, valkey, chroma, kafka)
#   app/      orchestrator source (git snapshot, bind-mounted over /app)
#   config/   moe_userdb_schema.sql, moe_userdb_config_data.sql, lumi.env (no secrets)
#   data/     routing/advice runtime data (bind-mounted to /app/data)
#   runs/<jobid>/  logs, results, runtime secrets (0700)
# Ports on 127.0.0.1: orchestrator 8000, mcp 8003, shim 18100, chroma 18001, postgres 15432, valkey 16379,
#   neo4j 7474/7687, kafka 9092/9093, embed 21434, vLLM 8101..8111.
#SBATCH --job-name=moe_stack
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --output=/scratch/project_465003058/hornphil/moe-stack/logs/stack_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-stack/logs/stack_%j.err
set -euo pipefail
SCRATCH=/scratch/project_465003058/hornphil
R=$SCRATCH/moe-stack
RUN=$R/runs/$SLURM_JOB_ID
SHM=/dev/shm/moe_$SLURM_JOB_ID
CK=$SCRATCH/checkpoints
mkdir -p "$RUN/logs" "$SHM" && chmod 700 "$RUN" "$SHM"
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done; sleep 5; rm -rf "$SHM"; }
trap cleanup EXIT
log() { echo "[$(date +%H:%M:%S)] $*"; }
wait_http() {  # url name [tries] [pid: fail fast once that process is gone]
    for _ in $(seq "${3:-180}"); do
        curl -sf -m 3 "$1" >/dev/null 2>&1 && { log "ready: $2"; return 0; }
        [ -n "${4:-}" ] && ! kill -0 "$4" 2>/dev/null && { log "DIED: $2 (see logs)"; return 1; }
        sleep 2
    done; log "TIMEOUT: $2 ($1)"; return 1; }
bg() { local name=$1; shift; "$@" > "$RUN/logs/$name.log" 2>&1 & LASTPID=$!; PIDS+=($LASTPID); }
SX() { singularity exec --bind "$SCRATCH:$SCRATCH" --bind "$SHM:$SHM" "$@"; }

# ---- runtime secrets (never leave $RUN) ------------------------------------------------------
PG_PASS=$(openssl rand -hex 16); REDIS_PASS=$(openssl rand -hex 16); SYS_KEY="moe-sk-$(openssl rand -hex 24)"; NEO_PASS=$(openssl rand -hex 12)
umask 077
cp "$R/config/lumi.env" "$RUN/stack.env"
cat >> "$RUN/stack.env" <<EOF
REDIS_URL=redis://:$REDIS_PASS@127.0.0.1:16379/0
POSTGRES_CHECKPOINT_URL=postgresql://langgraph:$PG_PASS@127.0.0.1:15432/langgraph
MOE_USERDB_URL=postgresql://moe_admin:$PG_PASS@127.0.0.1:15432/moe_userdb
CHROMA_HOST=127.0.0.1
CHROMA_PORT=18001
MCP_URL=http://127.0.0.1:8003
KAFKA_URL=kafka://127.0.0.1:9092
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USER=neo4j
NEO4J_PASS=$NEO_PASS
SEMANTIC_MEMORY_EMBED_URL=http://127.0.0.1:21434
SYSTEM_API_KEY=$SYS_KEY
HF_HUB_OFFLINE=1
EOF
umask 022

# ---- PostgreSQL ------------------------------------------------------------------------------
PG="$R/sif/postgres_17-alpine.sif"
SX "$PG" initdb -D "$SHM/pg" -U postgres --auth=trust >/dev/null
mkdir -p "$SHM/pgsock"
bg postgres SX "$PG" postgres -D "$SHM/pg" -p 15432 -k "$SHM/pgsock" -c listen_addresses=127.0.0.1 -c fsync=off -c max_connections=300
for _ in $(seq 60); do SX "$PG" pg_isready -h 127.0.0.1 -p 15432 >/dev/null 2>&1 && break; sleep 1; done
PSQL() { SX "$PG" psql -h 127.0.0.1 -p 15432 -U postgres -v ON_ERROR_STOP=1 -q "$@"; }
PSQL -c "CREATE ROLE langgraph LOGIN PASSWORD '$PG_PASS'; CREATE ROLE moe_admin LOGIN PASSWORD '$PG_PASS';"
PSQL -c "CREATE DATABASE langgraph OWNER langgraph" -c "CREATE DATABASE moe_userdb OWNER moe_admin"  # separate -c: no implicit transaction
PSQL -d moe_userdb -c "SET ROLE moe_admin" -f "$R/config/moe_userdb_schema.sql" >/dev/null
PSQL -d moe_userdb -c "SET ROLE moe_admin" -f "$R/config/moe_userdb_config_data.sql" >/dev/null
log "postgres loaded: $(PSQL -d moe_userdb -At -c 'SELECT count(*) FROM admin_expert_templates') templates"

# ---- Valkey, Chroma, Kafka, Neo4j, embeddings ------------------------------------------------
bg valkey SX "$R/sif/valkey_valkey_8-alpine.sif" valkey-server --bind 127.0.0.1 --port 16379 --requirepass "$REDIS_PASS" --save "" --appendonly no
bg chroma env IS_PERSISTENT=TRUE ANONYMIZED_TELEMETRY=FALSE singularity exec --bind "$SHM:$SHM" "$R/sif/chromadb_chroma_1.5.9.sif" chroma run --host 127.0.0.1 --port 18001 --path "$SHM/chroma"
KS="$R/sif/apache_kafka_3.7.1.sif"; mkdir -p "$SHM/kafka"
cat > "$SHM/kafka/server.properties" <<EOF
node.id=1
process.roles=broker,controller
controller.quorum.voters=1@127.0.0.1:9093
listeners=PLAINTEXT://127.0.0.1:9092,CONTROLLER://127.0.0.1:9093
advertised.listeners=PLAINTEXT://127.0.0.1:9092
listener.security.protocol.map=PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT
controller.listener.names=CONTROLLER
inter.broker.listener.name=PLAINTEXT
auto.create.topics.enable=true
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
log.dirs=$SHM/kafka/data
EOF
SX "$KS" /opt/kafka/bin/kafka-storage.sh format -t "$(SX "$KS" /opt/kafka/bin/kafka-storage.sh random-uuid)" -c "$SHM/kafka/server.properties" >/dev/null
bg kafka env LOG_DIR="$SHM/kafka/logs" singularity exec --bind "$SHM:$SHM" "$KS" /opt/kafka/bin/kafka-server-start.sh "$SHM/kafka/server.properties"
NR=$SCRATCH/neo4jtest; JH=$(ls -d $NR/jdk-21*); cp -r "$NR/neo4j-community-5.26.0" "$SHM/neo4j"
sed -i 's/^#dbms.security.auth_enabled=false/dbms.security.auth_enabled=false/' "$SHM/neo4j/conf/neo4j.conf"
printf 'server.memory.heap.max_size=8g\nserver.memory.pagecache.size=4g\nserver.bolt.listen_address=127.0.0.1:7687\nserver.http.listen_address=127.0.0.1:7474\n' >> "$SHM/neo4j/conf/neo4j.conf"
JAVA_HOME="$JH" PATH="$JH/bin:$PATH" "$SHM/neo4j/bin/neo4j" start > "$RUN/logs/neo4j.log" 2>&1
# --env, not a host env: the image sets OLLAMA_HOST itself, which beats inherited variables (smoke 23514795 listened on 11434)
bg embed env HIP_VISIBLE_DEVICES= ROCR_VISIBLE_DEVICES= singularity exec --bind "$SCRATCH:$SCRATCH" \
    --env "OLLAMA_HOST=127.0.0.1:21434,OLLAMA_MODELS=$R/ollama-models/models,OLLAMA_NUM_GPU=0" "$R/sif/moe-embed_local.sif" ollama serve

# ---- vLLM servers (one per model) + Ollama shim -----------------------------------------------
VS="$SCRATCH/lumi-multitorch-latest.sif"
# .user_site = transformers 5.x overlay: the merged checkpoints were saved with it (rope_parameters format
# unknown to the container's transformers 4.57); same overlay as the vLLM-GRPO pilot
VENV="PYTHONPATH=$SCRATCH/.user_site,HF_HOME=$SCRATCH/cache/huggingface,XDG_CACHE_HOME=$SCRATCH/cache,TRITON_CACHE_DIR=$SCRATCH/cache/triton,TMPDIR=$SCRATCH/tmp,HF_HUB_OFFLINE=1"
SHIM_CFG="$RUN/shim.json"; SHIM_ROWS="$RUN/shim_rows.tsv"; : > "$SHIM_ROWS"; declare -A VPID
serve() {  # name gcds port path max_len mem_util tp family size
    local name=$1 gcds=$2 port=$3 path=$4 len=$5 util=$6 tp=$7 fam=$8 size=$9 ov=()
    # transformers 5 nests rope_parameters per layer type (OLMo-3); vLLM's olmo2.py expects the flat full-attention
    # dict and derives the sliding layers itself (default rope, same theta) -> pass that dict as override
    local flat; flat=$(python3 - "$path/config.json" <<'PY'
import json, sys
rp = json.load(open(sys.argv[1])).get("rope_parameters") or {}
fa = rp.get("full_attention")
if fa:
    sa = rp.get("sliding_attention", {})
    assert float(sa.get("rope_theta", fa["rope_theta"])) == float(fa["rope_theta"]), "sliding/full rope_theta differ"
    print(json.dumps({"rope_parameters": fa}))
PY
)
    [ -n "$flat" ] && ov=(--hf-overrides "$flat")
    # OLMo-3 on ROCm: CUDA-graph capture fails with hipErrorCapturedEvent (teacher smoke 21848558, Sept. 2026)
    [ "$fam" = olmo3 ] && ov+=(--enforce-eager)
    # ROCR only: HIP indices are renumbered after ROCR filtering, so HIP_VISIBLE_DEVICES=2 on top would hide the GCD
    bg "vllm_$port" env -u HIP_VISIBLE_DEVICES -u CUDA_VISIBLE_DEVICES ROCR_VISIBLE_DEVICES="$gcds" singularity exec --bind "$SCRATCH:$SCRATCH" --env "$VENV" "$VS" \
        python3 -m vllm.entrypoints.openai.api_server --model "$path" --served-model-name "$name" --host 127.0.0.1 --port "$port" \
        --max-model-len "$len" --gpu-memory-utilization "$util" --tensor-parallel-size "$tp" --dtype bfloat16 "${ov[@]}"
    VPID[$port]=$LASTPID
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$port" "$len" "$fam" "$size" >> "$SHIM_ROWS"   # same name twice = replicas
}
E=hf.co/h3rb3rn
# Two judge replicas (GCD pairs 0,1 and 6,7 = one MI250X card each): wave 2 (23522672-75) was judge-bound, with ~14 of
# 16 episodes always waiting on one judge (~9.5 tok/s each) while the experts averaged 0.17 running requests.
# The experts therefore share three GCDs (3 + 3 + 2 servers).
JUDGE="$E/sovereign-judge-olmo31-32b:Q4_K_M"
serve "$JUDGE" 0,1 8101 "$CK/merged_olmo31_32b_judge" 65536 0.90 2 olmo3 32B
serve "$JUDGE" 6,7 8111 "$CK/merged_olmo31_32b_judge" 65536 0.90 2 olmo3 32B
serve "$E/moe-sovereign-planner-olmo3-7b:Q4_K_M"   2   8102 "$CK/merged_olmo3_7b_planner" 65536 0.90 1 olmo3 7B
wait_http http://127.0.0.1:8102/v1/models planner 600 "${VPID[8102]}"
port=8103; i=0
for role in omni research security governance datainfra coder precision graphrag; do
    gcd=$((3 + i / 3))
    serve "$E/smollm3-expert-$role-3b:Q4_K_M" "$gcd" "$port" "$CK/merged_smollm3_expert_$role" 48128 0.29 1 smollm3 3B
    wait_http "http://127.0.0.1:$port/v1/models" "expert $role" 600 "${VPID[$port]}"   # sequential start: later servers on a GCD size against free memory
    port=$((port + 1)); i=$((i + 1))
done
python3 - "$SHIM_ROWS" > "$SHIM_CFG" <<'PY'
import json, sys
models = {}
for line in open(sys.argv[1]):
    name, port, ctx, fam, size = line.rstrip("\n").split("\t")
    m = models.setdefault(name, {"urls": [], "context_length": int(ctx), "family": fam, "parameter_size": size})
    m["urls"].append(f"http://127.0.0.1:{port}")
print(json.dumps({"models": models, "embed_url": "http://127.0.0.1:21434"}, indent=1))
PY
wait_http http://127.0.0.1:8101/v1/models judge 900 "${VPID[8101]}"
wait_http http://127.0.0.1:8111/v1/models judge-2 900 "${VPID[8111]}"
OS="$R/sif/moe-sovereign-orchestrator_local.sif"
bg shim SX "$OS" python3 "$R/app/benchmarks/orchestration_suite/lumi/ollama_shim.py" --config "$SHIM_CFG" --port 18100
wait_http http://127.0.0.1:18100/api/tags shim 60

# ---- MCP precision + orchestrator --------------------------------------------------------------
mkdir -p "$RUN/mcp_generated"   # /app is read-only in the SIF (smoke 23515108: MCP died on mkdir /app/generated)
bg mcp SX --env-file "$RUN/stack.env" --env "MOE_GENERATED_DIR=$RUN/mcp_generated" --pwd /app "$R/sif/moe-mcp-precision_lumi.sif" python server.py
MCP_PID=$LASTPID
mkdir -p "$RUN/applogs"
APPB=(--bind "$R/app:/app" --bind "$R/data:/app/data" --bind "$RUN/applogs:/app/logs" --bind "$RUN/stack.env:/app/.env" --env-file "$RUN/stack.env" --pwd /app)
wait_http http://127.0.0.1:18001/api/v2/heartbeat chroma 120
wait_http http://127.0.0.1:7474/ neo4j 120
wait_http http://127.0.0.1:21434/api/tags embed 120
wait_http http://127.0.0.1:8003/health mcp 120 "$MCP_PID"   # orchestrator clears the tool catalog if MCP is down at startup
SX "${APPB[@]}" "$OS" python3 benchmarks/orchestration_suite/lumi/bootstrap_research_user.py --key-file "$RUN/research.key" \
    --templates tmpl-11f532fc,tmpl-smollm3-delib,tmpl-smollm3-nograph,tmpl-smollm3-review,tmpl-smollm3-review-nosc \
    --nodes N04-RGTX,N04-RTX,N02-M60-02,N02-M60-03,N02-M60-04,N02-M60-05,N02-M60-06,N02-M60-07,N02-M60-08,N02-M60-09
bg orchestrator SX "${APPB[@]}" "$OS" python -u main.py
wait_http http://127.0.0.1:8000/health orchestrator 600
log "stack up"

# ---- rollout driver ----------------------------------------------------------------------------
if [ -n "${TASKS:-}" ]; then
    SX "${APPB[@]}" --env SYSTEM_API_KEY="$SYS_KEY" "$OS" python3 benchmarks/orchestration_suite/rollout_driver.py \
        --api http://127.0.0.1:8000 --key-file "$RUN/research.key" --tasks "$R/$TASKS" \
        --template "${TEMPLATE:-LUMI-G OLMo + SmolLM3 Sovereign Ensemble}" --out "$RUN/rollouts.jsonl" \
        --n "${N:-0}" --offset "${OFFSET:-0}" --forks "${FORKS:-4}" --concurrency "${CONC:-16}" ${DRIVER_ARGS:-} | tee "$RUN/driver_summary.json"
else
    log "no TASKS given: keeping the stack alive for ${HOLD_MIN:-30} min"; sleep $(( ${HOLD_MIN:-30} * 60 ))
fi
