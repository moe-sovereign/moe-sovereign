#!/bin/bash
# Orchestration PRM (phase 2): scalar value head on OLMo-3(.1), all parameters, DeepSpeed ZeRO-3, 1..N nodes.
#
# Smoke (7B, 1 node, 20 steps):
#   sbatch --nodes=1 --time=01:00:00 --export=ALL,MODEL=allenai/Olmo-3-7B-Instruct,MAX_STEPS=20,MAX_TRAIN_ROWS=2000,MAX_EVAL_ROWS=200,TAG=smoke7b lumig_prm_train.slurm
# Real run (32B): 4 nodes fit small-g; more need --partition=standard-g
#   sbatch --nodes=4 --time=2-00:00:00 --export=ALL,TAG=prm32b lumig_prm_train.slurm
# Memory: ZeRO-3 shards bf16 params, grads and fp32 Adam state (~16 B/param = 512 GB for 32B) over all GCDs; one node
# (8 x 64 GB) is too small for 32B, 4 nodes leave ~30 GB per GCD for activations (gradient checkpointing on).
#
#SBATCH --job-name=moe_prm
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=4
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=2-00:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/prm/logs/prm_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/prm/logs/prm_%j.err

set -euo pipefail
S=/scratch/project_465003058/hornphil
P=$S/prm
SIF=$S/lumi-multitorch-latest.sif
MODEL=${MODEL:-allenai/Olmo-3.1-32B-Instruct}
TAG=${TAG:-prm}
DATA=${DATA:-$P/data}
OUT=$P/runs/${TAG}_$SLURM_JOB_ID
mkdir -p "$OUT" "$P/logs"
[ -f "$DATA/train.jsonl" ] || { echo "no $DATA/train.jsonl"; exit 1; }

# rendezvous on the first node; one torchrun per node, 8 ranks (one per GCD) each
MASTER=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -n1)
echo "job $SLURM_JOB_ID nodes=$SLURM_JOB_NUM_NODES master=$MASTER model=$MODEL data=$DATA out=$OUT"

# Slingshot: RCCL over the four hsn NICs; set NCCL_DEBUG=INFO via --export to see which transport RCCL picked
ENVS="PYTHONPATH=$S/.user_site,HF_HOME=$S/hf_cache,HF_HUB_OFFLINE=1,XDG_CACHE_HOME=$S/cache,TRITON_CACHE_DIR=$S/cache/triton,TORCH_EXTENSIONS_DIR=$S/cache/torch_ext,TMPDIR=$S/tmp"
ENVS="$ENVS,NCCL_NET_GDR_LEVEL=PHB,NCCL_DEBUG=${NCCL_DEBUG:-WARN},PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,OMP_NUM_THREADS=7"
export SINGULARITYENV_NCCL_SOCKET_IFNAME=hsn0,hsn1,hsn2,hsn3   # SINGULARITYENV_ avoids the comma split of --env
mkdir -p "$S/cache/triton" "$S/cache/torch_ext" "$S/tmp"

srun --ntasks-per-node=1 singularity exec --bind "$S:$S" --env "$ENVS" "$SIF" \
    bash -c "torchrun --nnodes=$SLURM_JOB_NUM_NODES --nproc_per_node=8 --node_rank=\$SLURM_NODEID \
      --rdzv_backend=c10d --rdzv_endpoint=$MASTER:29500 --rdzv_id=$SLURM_JOB_ID \
      $P/app/train_prm.py --base-model '$MODEL' --data-dir '$DATA' --output-dir '$OUT' \
      --deepspeed $P/app/ds_zero3_prm.json \
      --epochs ${EPOCHS:-2} --lr ${LR:-5e-6} --grad-accum ${GRAD_ACCUM:-4} --max-len ${MAX_LEN:-5120} \
      --max-steps ${MAX_STEPS:--1} --max-train-rows ${MAX_TRAIN_ROWS:-0} --max-eval-rows ${MAX_EVAL_ROWS:-0} --save-steps ${SAVE_STEPS:-200}"
echo "done: $OUT"
