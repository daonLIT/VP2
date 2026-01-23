# app/services/hmm/runner.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple
import math

from app.services.emotion.emoti_shing_hmm import (
    STATES, EMOTIONS, STATE_TO_IDX, EMO_TO_IDX,
    A, _B as B, _PI as PI,
    normalize_emotion, viterbi_decode
)

def _forward_backward_gamma(obs_norm: Sequence[str]) -> Tuple[List[List[float]], float]:
    """
    Scaled Forward-Backward to compute posterior gamma[t][j] = P(state_j at t | obs)
    Returns:
      - gamma: T x N
      - loglik: log P(obs)
    """
    T = len(obs_norm)
    N = len(STATES)
    obs_idx = [EMO_TO_IDX[o] for o in obs_norm]

    # alpha, beta with scaling factors c[t]
    alpha = [[0.0] * N for _ in range(T)]
    beta  = [[0.0] * N for _ in range(T)]
    c     = [0.0] * T

    # init alpha[0]
    o0 = obs_idx[0]
    s = 0.0
    for j in range(N):
        alpha[0][j] = PI[j] * B[j][o0]
        s += alpha[0][j]
    if s <= 0:
        # degenerate
        gamma = [[1.0 / N] * N for _ in range(T)]
        return gamma, float("-inf")

    c[0] = 1.0 / s
    for j in range(N):
        alpha[0][j] *= c[0]

    # forward
    for t in range(1, T):
        ot = obs_idx[t]
        s = 0.0
        for j in range(N):
            acc = 0.0
            for i in range(N):
                acc += alpha[t - 1][i] * A[i][j]
            alpha[t][j] = acc * B[j][ot]
            s += alpha[t][j]
        if s <= 0:
            # fallback
            c[t] = 1.0
        else:
            c[t] = 1.0 / s
            for j in range(N):
                alpha[t][j] *= c[t]

    # init beta[T-1]
    for j in range(N):
        beta[T - 1][j] = 1.0

    # backward (scaled)
    for t in range(T - 2, -1, -1):
        ot1 = obs_idx[t + 1]
        for i in range(N):
            acc = 0.0
            for j in range(N):
                acc += A[i][j] * B[j][ot1] * beta[t + 1][j]
            beta[t][i] = acc
        # scaling: multiply by c[t+1]
        for i in range(N):
            beta[t][i] *= c[t + 1]

    # gamma
    gamma: List[List[float]] = []
    for t in range(T):
        row = [alpha[t][j] * beta[t][j] for j in range(N)]
        s = sum(row)
        if s <= 0:
            row = [1.0 / N] * N
        else:
            row = [v / s for v in row]
        gamma.append(row)

    # loglik = -sum(log(c[t]))
    loglik = 0.0
    for t in range(T):
        if c[t] > 0:
            loglik -= math.log(c[t])
    return gamma, loglik


def run_hmm_on_emotions(emotion_seq: List[str]) -> Optional[Dict[str, Any]]:
    """
    label_turns.py가 기대하는 포맷으로 HMM 결과를 반환한다.

    Returns (권장 포맷):
    {
      "state_names": ["v1","v2","v3"],
      "gamma": [[p1,p2,p3], ...],      # T x 3
      "path": ["v1","v2","v3", ...],   # T
      "final_state": "v2",
      "final_probs": [p1,p2,p3],
      "meta": {...}
    }
    """
    if not emotion_seq:
        return None

    # 1) 관측치 정규화: neutral/anger/fear/excitement
    obs_norm = [normalize_emotion(x) for x in emotion_seq]

    # 2) Viterbi path
    vit = viterbi_decode(obs_norm)
    # vit.states are "V1"/"V2"/"V3" -> lower "v1"/"v2"/"v3"
    path = [s.lower() for s in vit.states]
    final_state = vit.final_state.lower()

    # 3) Posterior gamma + final_probs
    gamma, loglik = _forward_backward_gamma(obs_norm)
    final_probs = gamma[-1] if gamma else None

    return {
        "state_names": ["v1", "v2", "v3"],
        "gamma": gamma,
        "path": path,
        "final_state": final_state,
        "final_probs": final_probs,
        "meta": {
            "obs_norm": obs_norm,
            "state_counts": vit.state_counts,
            "v3_ratio": vit.v3_ratio,
            "viterbi_logp": vit.logp,
            "loglik": loglik,
        },
    }
