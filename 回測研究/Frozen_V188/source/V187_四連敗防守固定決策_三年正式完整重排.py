#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V187｜V186四連敗防守固定決策｜三年正式完整重排
================================================

目的
----
V186 影子稽核已通過：
- 控制組 V157/V185A：220筆 / 77 TP1 / 35.00% / +3377.7408U
- V186 MAIN 影子：204筆 / 76 TP1 / 37.2549% / +3510.9716U
- 研究區 2023+2024 通過
- 2025 獨立驗證通過
- 三年合計通過

V187 不再改參數。
固定沿用 V186 MAIN：
    連續4筆已結束交易未命中TP1
    -> 接下來3個「原V157正式成交機會」進防守
    -> V185預測4R機率 < 20% 的交易跳過

本版重點
--------
V186 只做影子刪除，未重新：
- MAX5
- NEW2
- cooldown
- 動態1%資金
- 被跳過後釋放的倉位替補

V187 將 V186 在控制路徑上因果產生的 16 筆跳過決策，
以「固定事件黑名單」方式放回 V157 底層 transform，
然後交給原 V156/V157 引擎完整三年重跑。

為什麼先固定16筆，而不是直接做動態狀態機？
------------------------------------------
V186 的研究規格是依「V157正式成交流」定義下一個3個機會。
若直接在底層改成所有候選都計數，會偷偷改變 V186 的研究定義。

所以 V187 專門回答：
「V186 已驗證的16個因果跳過決策，一旦讓正式組合重新排倉，
到底還能不能贏 V157？」

若 V187 正式重排仍通過，
下一版 V188 才把四連敗防守做成真正實盤動態狀態機。

固定規則
--------
- 控制組：V157 / V162 / V185A 等價
- V85頂級救援：完整保留
- V85 / V96 Entry、Stop、TP：完全不修改
- 僅阻擋 V186 MAIN 在 V157控制路徑中決定跳過的16筆
- 20%門檻、4連敗、3筆防守完全固定
- 不用月份當條件
- 不因2025/2026結果再調參數

正式升級要求
------------
1. 16筆固定跳過全部實際命中底層
2. 三年淨利 > V157
3. 勝率 >= V157
4. 最大年度回撤 <= V157
5. 交易數保留 >= 85%
6. 至少2個年度相對V157淨利 >= 0
7. 2025獨立驗證年度相對V157淨利 >= 0

全部通過：
PASS_可進V188實盤動態狀態機整合
"""

from __future__ import annotations

import heapq
import importlib.util
import io
import json
import math
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd


# =============================================================================
# 路徑 / 固定規格
# =============================================================================

HOME = Path.home()
CRYPTO = HOME / "CryptoRadar"

BASE_SCRIPT_NAME = "backtest_1y_v156_high_confidence_failure_guard_3y.py"

BASELINE_DIR_NAMES = [
    "V162_單標的自身量能百分位_三年結果",
    "V157_V85頂級救援_三年結果",
]

V185_ZIP_NAME = "V185_四R能力辨識選擇性三R救援_三年結果.zip"

OUT = (
    CRYPTO
    / "V187_四連敗防守固定決策_三年正式完整重排"
)

CONTROL_EXPECTED = {
    "trades": 220,
    "tp1": 77,
    "pnl": 3377.7408,
}

START_EQUITY_U = 1000.0

LOSS_TRIGGER = 4
DEFENSE_ENTRIES = 3
P4R_MIN = 0.20

# V157 頂級救援固定規格
V157_RESCUE_MAX_STOP_PCT = 0.023
V157_RESCUE_MIN_VOLUME_BUILD = 1.90
V157_RESCUE_MAX_RET8 = 0.01

EXPECTED_V186_BLOCKS = 16
EXPECTED_V186_BLOCKED_TP1 = 1

BLOCK_HITS: set[tuple[str, str, str]] = set()
BLOCK_AUDIT: list[dict[str, Any]] = []


# =============================================================================
# 共用
# =============================================================================

def read_csv(path: Path) -> pd.DataFrame:
    last = None

    for enc in (
        "utf-8-sig",
        "utf-8",
        "cp950",
    ):
        try:
            return pd.read_csv(
                path,
                encoding=enc,
                low_memory=False,
            )
        except Exception as exc:
            last = exc

    raise RuntimeError(
        f"CSV讀取失敗：{path}\n{last}"
    )


def number(
    v: Any,
    default: float = math.nan,
) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def load_module(
    path: Path,
    name: str,
):
    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"無法載入：{path}"
        )

    m = importlib.util.module_from_spec(
        spec
    )

    sys.modules[name] = m
    spec.loader.exec_module(m)

    return m


def find_file_recursive(
    name: str,
) -> Path | None:
    direct = [
        HOME / "Downloads" / name,
        CRYPTO / name,
        HOME / "Desktop" / "回測專案" / name,
        HOME / "Desktop" / "回測專案" / "V006_1實盤等價回測" / name,
        HOME / "Documents" / "ChatGPT" / "回測專案" / name,
        HOME / "Documents" / "ChatGPT" / "回測專案" / "V006_1實盤等價回測" / name,
        HOME / "Documents" / "回測專案" / name,
    ]

    for p in direct:
        if p.exists():
            return p

    roots = [
        HOME / "Downloads",
        CRYPTO,
        HOME / "Desktop" / "回測專案",
        HOME / "Documents" / "ChatGPT" / "回測專案",
        HOME / "Documents" / "回測專案",
    ]

    for root in roots:
        if not root.exists():
            continue

        try:
            hits = list(
                root.rglob(name)
            )

            if hits:
                return hits[0]
        except Exception:
            pass

    return None


def find_base_script() -> Path:
    p = find_file_recursive(
        BASE_SCRIPT_NAME
    )

    if p is None:
        raise FileNotFoundError(
            f"找不到 {BASE_SCRIPT_NAME}"
        )

    return p


def find_v185_zip() -> Path:
    p = find_file_recursive(
        V185_ZIP_NAME
    )

    if p is not None:
        return p

    # fallback：找名字有 V185 + 四R能力的 zip
    roots = [
        HOME / "Downloads",
        CRYPTO,
        HOME / "Desktop",
        HOME / "Documents",
    ]

    found = []

    for root in roots:
        if not root.exists():
            continue

        try:
            for q in root.rglob("*.zip"):
                n = q.name

                if (
                    "V185" in n
                    and "四R能力" in n
                ):
                    found.append(q)
        except Exception:
            pass

    if not found:
        raise FileNotFoundError(
            "找不到 V185 三年結果 zip"
        )

    found.sort(
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    return found[0]


def normalize_exchange(
    v: Any,
) -> str:
    s = str(
        v or ""
    ).strip().upper()

    if "BINANCE" in s:
        return "BINANCE"

    if "BITGET" in s:
        return "BITGET"

    if "BINGX" in s:
        return "BINGX"

    return "".join(
        ch
        for ch in s
        if ch.isalnum()
    )


def normalize_symbol(
    v: Any,
) -> str:
    s = str(
        v or ""
    ).strip().upper()

    for token in (
        "/",
        "-",
        "_",
        ":",
        " ",
        ".",
    ):
        s = s.replace(
            token,
            "",
        )

    if s.endswith(
        "USDTUSDT"
    ):
        s = s[:-4]

    return s


def normalize_time(
    v: Any,
) -> str:
    t = pd.to_datetime(
        v,
        utc=True,
        errors="coerce",
    )

    if pd.isna(t):
        return ""

    # 所有正式 Entry 都落在 15m；
    # 以分鐘做key避免字串秒數格式差異。
    t = t.floor("min")

    return t.isoformat()


def trade_key(
    exchange: Any,
    symbol: Any,
    entry_time: Any,
) -> tuple[str, str, str]:
    return (
        normalize_exchange(
            exchange
        ),
        normalize_symbol(
            symbol
        ),
        normalize_time(
            entry_time
        ),
    )


# =============================================================================
# 控制組
# =============================================================================

def summary_totals(
    path: Path,
) -> tuple[
    int,
    int,
    float,
] | None:
    try:
        d = read_csv(path)
    except Exception:
        return None

    required = {
        "交易數",
        "TP1筆數",
        "已實現淨利_U",
    }

    if not required.issubset(
        d.columns
    ):
        return None

    if d.empty or len(d) > 20:
        return None

    trades = int(
        pd.to_numeric(
            d["交易數"],
            errors="coerce",
        ).fillna(0).sum()
    )

    tp1 = int(
        pd.to_numeric(
            d["TP1筆數"],
            errors="coerce",
        ).fillna(0).sum()
    )

    pnl = float(
        pd.to_numeric(
            d["已實現淨利_U"],
            errors="coerce",
        ).fillna(0).sum()
    )

    return (
        trades,
        tp1,
        pnl,
    )


def find_control_result() -> Path:
    roots = []

    for dirname in BASELINE_DIR_NAMES:
        root = (
            CRYPTO
            / dirname
        )

        if root.exists():
            roots.append(
                root
            )

    for root in roots:
        for p in root.rglob(
            "*.csv"
        ):
            x = summary_totals(
                p
            )

            if x is None:
                continue

            trades, tp1, pnl = x

            if (
                trades
                == CONTROL_EXPECTED[
                    "trades"
                ]
                and tp1
                == CONTROL_EXPECTED[
                    "tp1"
                ]
                and abs(
                    pnl
                    - CONTROL_EXPECTED[
                        "pnl"
                    ]
                )
                <= 0.10
            ):
                return p

    raise FileNotFoundError(
        "找不到正式 V157/V162 控制組："
        "220筆 / 77 TP1 / +3377.7408U"
    )


def totals(
    frame: pd.DataFrame,
) -> dict[str, Any]:
    trades = int(
        pd.to_numeric(
            frame["交易數"],
            errors="coerce",
        ).fillna(0).sum()
    )

    tp1 = int(
        pd.to_numeric(
            frame["TP1筆數"],
            errors="coerce",
        ).fillna(0).sum()
    )

    pnl = float(
        pd.to_numeric(
            frame[
                "已實現淨利_U"
            ],
            errors="coerce",
        ).fillna(0).sum()
    )

    dd = float(
        pd.to_numeric(
            frame[
                "最大回撤_pct"
            ],
            errors="coerce",
        ).max()
    )

    return {
        "交易數": trades,
        "TP1筆數": tp1,
        "總勝率_pct": (
            round(
                100.0
                * tp1
                / trades,
                4,
            )
            if trades
            else 0.0
        ),
        "三年淨利_U": round(
            pnl,
            4,
        ),
        "1000U期末權益_U": round(
            START_EQUITY_U
            + pnl,
            4,
        ),
        "最大年度回撤_pct": round(
            dd,
            4,
        ),
    }


# =============================================================================
# 從 V185A 重建 V186 MAIN 16筆固定跳過
# =============================================================================

def v185a_trade_members(
    zf: zipfile.ZipFile,
) -> dict[str, str]:
    members = {}

    for year in (
        "2023",
        "2024",
        "2025",
    ):
        suffix = (
            "V185A_最低四R機率10趴才救援/"
            "內部引擎結果/"
            f"{year}_V006.1實盤等價結果/"
            "全部成交.csv"
        )

        hit = next(
            (
                n
                for n
                in zf.namelist()
                if n.endswith(
                    suffix
                )
            ),
            None,
        )

        if hit is None:
            raise FileNotFoundError(
                f"V185 zip 缺 {year} 全部成交.csv"
            )

        members[
            year
        ] = hit

    return members


def read_zip_csv(
    zf: zipfile.ZipFile,
    member: str,
) -> pd.DataFrame:
    raw = zf.read(
        member
    )

    return pd.read_csv(
        io.BytesIO(raw),
        low_memory=False,
    )


def load_v185a_control_trades(
    zip_path: Path,
) -> pd.DataFrame:
    parts = []

    with zipfile.ZipFile(
        zip_path,
        "r",
    ) as zf:
        members = (
            v185a_trade_members(
                zf
            )
        )

        for year, member in (
            members.items()
        ):
            d = read_zip_csv(
                zf,
                member,
            )

            d["_年度"] = year
            parts.append(
                d
            )

    d = pd.concat(
        parts,
        ignore_index=True,
        sort=False,
    )

    required = {
        "exchange",
        "symbol",
        "entry_time",
        "final_exit_time_v92",
        "tp1_time_v92",
        "V185預測四R機率",
    }

    missing = (
        required
        - set(
            d.columns
        )
    )

    if missing:
        raise RuntimeError(
            "V185A 全部成交缺欄位："
            + ", ".join(
                sorted(
                    missing
                )
            )
        )

    d["_entry_time"] = (
        pd.to_datetime(
            d[
                "entry_time"
            ],
            utc=True,
            errors="coerce",
        )
    )

    d["_exit_time"] = (
        pd.to_datetime(
            d[
                "final_exit_time_v92"
            ],
            utc=True,
            errors="coerce",
        )
    )

    d["_tp1_time"] = (
        pd.to_datetime(
            d[
                "tp1_time_v92"
            ],
            utc=True,
            errors="coerce",
        )
    )

    d["_TP1"] = (
        d[
            "_tp1_time"
        ].notna()
    )

    d["_P4R"] = (
        pd.to_numeric(
            d[
                "V185預測四R機率"
            ],
            errors="coerce",
        )
    )

    if (
        "已實現淨利_USDT"
        in d.columns
    ):
        d["_官方逐筆U"] = (
            pd.to_numeric(
                d[
                    "已實現淨利_USDT"
                ],
                errors="coerce",
            )
            .fillna(0.0)
        )
    else:
        d["_官方逐筆U"] = (
            pd.to_numeric(
                d.get(
                    "realized_profit_usdt_v92"
                ),
                errors="coerce",
            )
            .fillna(0.0)
        )

    d = (
        d
        .dropna(
            subset=[
                "_entry_time"
            ]
        )
        .sort_values(
            [
                "_entry_time",
                "exchange",
                "symbol",
            ],
            kind="stable",
        )
        .reset_index(
            drop=True
        )
    )

    if len(d) != 220:
        raise RuntimeError(
            f"V185A 控制交易數 {len(d)} != 220"
        )

    if int(
        d["_TP1"].sum()
    ) != 77:
        raise RuntimeError(
            "V185A TP1 != 77"
        )

    return d


def derive_v186_blocks(
    control: pd.DataFrame,
) -> tuple[
    set[
        tuple[
            str,
            str,
            str,
        ]
    ],
    pd.DataFrame,
]:
    pending: list[
        tuple[
            int,
            int,
            bool,
            str,
            str,
        ]
    ] = []

    seq = 0
    loss_streak = 0
    defense_left = 0

    blocks = set()
    audits = []

    for _, row in (
        control.iterrows()
    ):
        current_time = row[
            "_entry_time"
        ]

        closed_events = []

        while (
            pending
            and pending[0][0]
            <= current_time.value
        ):
            (
                _,
                _,
                hit_tp1,
                old_symbol,
                old_entry,
            ) = heapq.heappop(
                pending
            )

            if hit_tp1:
                loss_streak = 0

                closed_events.append(
                    f"{old_symbol}:TP1"
                )
            else:
                loss_streak += 1

                closed_events.append(
                    f"{old_symbol}:NO_TP1"
                )

                if (
                    loss_streak
                    >= LOSS_TRIGGER
                ):
                    defense_left = max(
                        defense_left,
                        DEFENSE_ENTRIES,
                    )

        before = defense_left
        p4r = number(
            row["_P4R"]
        )

        in_defense = (
            before > 0
        )

        keep = True
        reason = "正常模式"

        if in_defense:
            if (
                math.isfinite(
                    p4r
                )
                and p4r
                < P4R_MIN
            ):
                keep = False

                reason = (
                    f"V186防守｜"
                    f"P4R={p4r:.4f}"
                    f"<{P4R_MIN:.2f}｜跳過"
                )
            elif math.isfinite(
                p4r
            ):
                reason = (
                    f"V186防守｜"
                    f"P4R={p4r:.4f}"
                    f">={P4R_MIN:.2f}｜保留"
                )
            else:
                reason = (
                    "V186防守｜P4R缺值｜保留"
                )

            defense_left = max(
                0,
                defense_left - 1,
            )

        if keep:
            exit_time = row[
                "_exit_time"
            ]

            if pd.notna(
                exit_time
            ):
                heapq.heappush(
                    pending,
                    (
                        exit_time.value,
                        seq,
                        bool(
                            row[
                                "_TP1"
                            ]
                        ),
                        str(
                            row.get(
                                "symbol"
                            )
                        ),
                        str(
                            row.get(
                                "entry_time"
                            )
                        ),
                    ),
                )

                seq += 1

        else:
            key = trade_key(
                row.get(
                    "exchange"
                ),
                row.get(
                    "symbol"
                ),
                row.get(
                    "entry_time"
                ),
            )

            blocks.add(
                key
            )

            audits.append({
                "年度": row[
                    "_年度"
                ],
                "exchange": row.get(
                    "exchange"
                ),
                "symbol": row.get(
                    "symbol"
                ),
                "module": row.get(
                    "module"
                ),
                "direction": row.get(
                    "direction_v92",
                    row.get(
                        "direction"
                    ),
                ),
                "entry_time": row.get(
                    "entry_time"
                ),
                "final_exit_time_v92": row.get(
                    "final_exit_time_v92"
                ),
                "tp1_time_v92": row.get(
                    "tp1_time_v92"
                ),
                "原TP1命中": bool(
                    row["_TP1"]
                ),
                "V185預測4R機率": p4r,
                "原官方逐筆U": float(
                    row[
                        "_官方逐筆U"
                    ]
                ),
                "進場前連敗數": loss_streak,
                "進場前防守剩餘機會": before,
                "本次已結束事件": "｜".join(
                    closed_events
                ),
                "V186判定": reason,
                "固定BlockKey": str(
                    key
                ),
            })

    audit_df = pd.DataFrame(
        audits
    )

    if len(
        blocks
    ) != EXPECTED_V186_BLOCKS:
        raise RuntimeError(
            f"V186 MAIN 重建跳過數 {len(blocks)} "
            f"!= {EXPECTED_V186_BLOCKS}"
        )

    blocked_tp1 = int(
        audit_df[
            "原TP1命中"
        ].sum()
    )

    if (
        blocked_tp1
        != EXPECTED_V186_BLOCKED_TP1
    ):
        raise RuntimeError(
            f"V186 跳過TP1數 {blocked_tp1} "
            f"!= {EXPECTED_V186_BLOCKED_TP1}"
        )

    return (
        blocks,
        audit_df,
    )


# =============================================================================
# V157 頂級救援
# =============================================================================

def patch_v85_elite_rescue(
    v155,
) -> None:
    original_transform = (
        v155.transform_v154
    )

    def transform_v157(
        row: dict,
        frame: pd.DataFrame,
    ):
        transformed, audit = (
            original_transform(
                row,
                frame,
            )
        )

        if transformed is not None:
            return (
                transformed,
                audit,
            )

        module_name = (
            v155.module_name(
                row
            ).upper()
        )

        reason = str(
            audit.get(
                "reason",
                "",
            )
        )

        is_v85_stop_reject = (
            "V85" in module_name
            and (
                reason.startswith(
                    "V85原停損過寬"
                )
                or reason.startswith(
                    "V85停損過寬"
                )
            )
        )

        if not is_v85_stop_reject:
            return (
                transformed,
                audit,
            )

        original_stop_pct = (
            v155.stop_pct(
                row
            )
        )

        features = (
            v155.v155_recalculate_features(
                row,
                frame,
            )
        )

        volume_build = (
            v155.number(
                features.get(
                    "volume_build"
                )
            )
        )

        ret8 = (
            v155.number(
                features.get(
                    "ret8"
                )
            )
        )

        eligible = (
            math.isfinite(
                original_stop_pct
            )
            and 0.02
            < original_stop_pct
            <= V157_RESCUE_MAX_STOP_PCT
            + 1e-12
            and bool(
                features.get(
                    "recalc_ok"
                )
            )
            and math.isfinite(
                volume_build
            )
            and volume_build
            >= V157_RESCUE_MIN_VOLUME_BUILD
            and math.isfinite(
                ret8
            )
            and ret8
            <= V157_RESCUE_MAX_RET8
            + 1e-12
        )

        audit[
            "V157救援檢查"
        ] = (
            "符合"
            if eligible
            else "不符合"
        )

        if not eligible:
            return (
                transformed,
                audit,
            )

        old_limit = (
            v155.V85_ORIGINAL_MAX_STOP_PCT
        )

        try:
            (
                v155.V85_ORIGINAL_MAX_STOP_PCT
            ) = (
                V157_RESCUE_MAX_STOP_PCT
            )

            (
                rescued,
                rescued_audit,
            ) = original_transform(
                row,
                frame,
            )

        finally:
            (
                v155.V85_ORIGINAL_MAX_STOP_PCT
            ) = old_limit

        rescued_audit[
            "V157救援檢查"
        ] = "符合"

        if rescued is not None:
            rescued[
                "V157頂級救援"
            ] = True

            rescued[
                "V157救援原停損_pct"
            ] = (
                original_stop_pct
                * 100.0
            )

            rescued[
                "V157救援量能堆積"
            ] = volume_build

            rescued[
                "V157救援進場前8根漲幅_pct"
            ] = (
                ret8
                * 100.0
            )

            rescued_audit[
                "reason"
            ] = (
                "PASS_V157頂級救援｜"
                + str(
                    rescued_audit.get(
                        "reason",
                        "",
                    )
                )
            )

        return (
            rescued,
            rescued_audit,
        )

    v155.transform_v154 = (
        transform_v157
    )


# =============================================================================
# V187：固定16筆 block，讓正式引擎重排
# =============================================================================

def patch_v187_fixed_blocks(
    v155,
    block_keys: set[
        tuple[
            str,
            str,
            str,
        ]
    ],
) -> None:
    original_transform = (
        v155.transform_v154
    )

    def transform_v187(
        row: dict,
        frame: pd.DataFrame,
    ):
        transformed, audit = (
            original_transform(
                row,
                frame,
            )
        )

        if transformed is None:
            return (
                transformed,
                audit,
            )

        key = trade_key(
            row.get(
                "exchange"
            ),
            row.get(
                "symbol"
            ),
            transformed.get(
                "entry_time",
                row.get(
                    "entry_time"
                ),
            ),
        )

        if key not in block_keys:
            return (
                transformed,
                audit,
            )

        BLOCK_HITS.add(
            key
        )

        BLOCK_AUDIT.append({
            "exchange": row.get(
                "exchange"
            ),
            "symbol": row.get(
                "symbol"
            ),
            "module": (
                v155.module_name(
                    row
                )
            ),
            "direction": (
                v155.side(
                    row
                )
            ),
            "原始Entry時間": row.get(
                "entry_time"
            ),
            "V157最終Entry時間": (
                transformed.get(
                    "entry_time"
                )
            ),
            "V157最終Entry": (
                transformed.get(
                    "entry"
                )
            ),
            "V157最終Stop": (
                transformed.get(
                    "stop"
                )
            ),
            "V187動作": (
                "BLOCK_V186四連敗防守固定決策"
            ),
            "BlockKey": str(
                key
            ),
        })

        audit[
            "keep"
        ] = False

        audit[
            "V187防守模式"
        ] = (
            "BLOCK_V186四連敗防守固定決策"
        )

        audit[
            "reason"
        ] = (
            "BLOCK_V187｜"
            "沿用V186 MAIN已驗證之因果跳過決策"
        )

        return (
            None,
            audit,
        )

    v155.transform_v154 = (
        transform_v187
    )


# =============================================================================
# 讀取正式成交 / 比較
# =============================================================================

def infer_year_from_path(
    p: Path,
) -> str | None:
    s = p.as_posix()

    for y in (
        "2023",
        "2024",
        "2025",
    ):
        if (
            f"{y}_V006"
            in s
            or f"/{y}_"
            in s
            or f"\\{y}_"
            in s
        ):
            return y

    return None


def collect_full_trades(
    root: Path,
) -> pd.DataFrame:
    parts = []

    for p in root.rglob(
        "全部成交.csv"
    ):
        year = (
            infer_year_from_path(
                p
            )
        )

        if year is None:
            continue

        try:
            d = read_csv(
                p
            )
        except Exception:
            continue

        if not {
            "exchange",
            "symbol",
            "entry_time",
        }.issubset(
            d.columns
        ):
            continue

        d["_年度"] = year
        d["_來源檔"] = str(
            p
        )

        parts.append(
            d
        )

    if not parts:
        raise FileNotFoundError(
            "V187 找不到內部引擎全部成交.csv"
        )

    d = pd.concat(
        parts,
        ignore_index=True,
        sort=False,
    )

    d["_entry_time"] = (
        pd.to_datetime(
            d[
                "entry_time"
            ],
            utc=True,
            errors="coerce",
        )
    )

    d["_key"] = [
        trade_key(
            ex,
            sym,
            t,
        )
        for ex, sym, t
        in zip(
            d["exchange"],
            d["symbol"],
            d["entry_time"],
        )
    ]

    d = (
        d
        .dropna(
            subset=[
                "_entry_time"
            ]
        )
        .sort_values(
            "_entry_time",
            kind="stable",
        )
        .reset_index(
            drop=True
        )
    )

    return d


def attach_key(
    d: pd.DataFrame,
) -> pd.DataFrame:
    x = d.copy()

    x["_key"] = [
        trade_key(
            ex,
            sym,
            t,
        )
        for ex, sym, t
        in zip(
            x["exchange"],
            x["symbol"],
            x["entry_time"],
        )
    ]

    return x


def tp1_bool(
    d: pd.DataFrame,
) -> pd.Series:
    if (
        "tp1_time_v92"
        in d.columns
    ):
        return pd.to_datetime(
            d[
                "tp1_time_v92"
            ],
            utc=True,
            errors="coerce",
        ).notna()

    if (
        "TP1"
        in d.columns
    ):
        return (
            pd.to_numeric(
                d["TP1"],
                errors="coerce",
            )
            > 0
        )

    return pd.Series(
        False,
        index=d.index,
    )


def compare_trade_sets(
    control: pd.DataFrame,
    final: pd.DataFrame,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
]:
    c = attach_key(
        control
    )

    f = final.copy()

    control_keys = set(
        c["_key"]
    )

    final_keys = set(
        f["_key"]
    )

    added_keys = (
        final_keys
        - control_keys
    )

    removed_keys = (
        control_keys
        - final_keys
    )

    added = f[
        f["_key"].isin(
            added_keys
        )
    ].copy()

    removed = c[
        c["_key"].isin(
            removed_keys
        )
    ].copy()

    return (
        added,
        removed,
    )


def year_compare(
    control_df: pd.DataFrame,
    final_df: pd.DataFrame,
) -> pd.DataFrame:
    c = control_df.copy()
    f = final_df.copy()

    c["期間"] = (
        c["期間"].astype(str)
    )

    f["期間"] = (
        f["期間"].astype(str)
    )

    rows = []

    periods = sorted(
        set(
            c["期間"]
        )
        | set(
            f["期間"]
        )
    )

    for period in periods:
        cr = c[
            c["期間"].eq(
                period
            )
        ]

        fr = f[
            f["期間"].eq(
                period
            )
        ]

        if cr.empty or fr.empty:
            continue

        cr = cr.iloc[0]
        fr = fr.iloc[0]

        c_trades = int(
            number(
                cr.get(
                    "交易數"
                ),
                0,
            )
        )

        f_trades = int(
            number(
                fr.get(
                    "交易數"
                ),
                0,
            )
        )

        c_tp1 = int(
            number(
                cr.get(
                    "TP1筆數"
                ),
                0,
            )
        )

        f_tp1 = int(
            number(
                fr.get(
                    "TP1筆數"
                ),
                0,
            )
        )

        c_pnl = number(
            cr.get(
                "已實現淨利_U"
            ),
            0.0,
        )

        f_pnl = number(
            fr.get(
                "已實現淨利_U"
            ),
            0.0,
        )

        c_dd = number(
            cr.get(
                "最大回撤_pct"
            ),
        )

        f_dd = number(
            fr.get(
                "最大回撤_pct"
            ),
        )

        rows.append({
            "期間": period,
            "V157交易數": c_trades,
            "V187交易數": f_trades,
            "交易數變化": (
                f_trades
                - c_trades
            ),
            "V157_TP1": c_tp1,
            "V187_TP1": f_tp1,
            "TP1變化": (
                f_tp1
                - c_tp1
            ),
            "V157淨利_U": c_pnl,
            "V187淨利_U": f_pnl,
            "淨利變化_U": round(
                f_pnl
                - c_pnl,
                4,
            ),
            "V157最大回撤_pct": c_dd,
            "V187最大回撤_pct": f_dd,
            "回撤變化_百分點": (
                round(
                    f_dd
                    - c_dd,
                    4,
                )
                if (
                    math.isfinite(
                        f_dd
                    )
                    and math.isfinite(
                        c_dd
                    )
                )
                else math.nan
            ),
        })

    return pd.DataFrame(
        rows
    )


def monthly_2026(
    d: pd.DataFrame,
    label: str,
) -> pd.DataFrame:
    if d.empty:
        return pd.DataFrame()

    x = d.copy()

    t = pd.to_datetime(
        x[
            "entry_time"
        ],
        utc=True,
        errors="coerce",
    ).dt.tz_convert(
        "Asia/Taipei"
    )

    x["_year"] = t.dt.year
    x["_month"] = t.dt.month
    x["_TP1"] = tp1_bool(
        x
    )

    x = x[
        x["_year"].eq(
            2026
        )
    ].copy()

    if x.empty:
        return pd.DataFrame()

    g = (
        x.groupby(
            "_month",
            sort=True,
        )
        .agg(
            交易數=(
                "_TP1",
                "size",
            ),
            TP1=(
                "_TP1",
                "sum",
            ),
        )
        .reset_index()
        .rename(
            columns={
                "_month": "月份"
            }
        )
    )

    g[
        "勝率_pct"
    ] = (
        100.0
        * g["TP1"]
        / g["交易數"]
    ).round(4)

    g["版本"] = label

    return g[
        [
            "版本",
            "月份",
            "交易數",
            "TP1",
            "勝率_pct",
        ]
    ]


# =============================================================================
# 主程式
# =============================================================================

def main() -> int:
    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(
        "=" * 122
    )

    print(
        "V187｜V186四連敗防守固定決策｜三年正式完整重排"
    )

    print(
        "固定：4連敗 -> 防守3個V157正式成交機會 -> P4R<20%跳過"
    )

    print(
        "本版不調參；只把V186的16筆因果跳過放回正式引擎重跑。"
    )

    print(
        "=" * 122
    )

    # -----------------------------------------------------------------
    # 1. 控制組 / V185A / V186固定block
    # -----------------------------------------------------------------

    base_script = (
        find_base_script()
    )

    control_result = (
        find_control_result()
    )

    v185_zip = (
        find_v185_zip()
    )

    control_summary = read_csv(
        control_result
    )

    control_total = totals(
        control_summary
    )

    if (
        control_total[
            "交易數"
        ]
        != CONTROL_EXPECTED[
            "trades"
        ]
        or control_total[
            "TP1筆數"
        ]
        != CONTROL_EXPECTED[
            "tp1"
        ]
        or abs(
            control_total[
                "三年淨利_U"
            ]
            - CONTROL_EXPECTED[
                "pnl"
            ]
        )
        > 0.10
    ):
        raise RuntimeError(
            "控制組驗收失敗："
            + str(
                control_total
            )
        )

    control_trades = (
        load_v185a_control_trades(
            v185_zip
        )
    )

    (
        block_keys,
        v186_block_audit,
    ) = derive_v186_blocks(
        control_trades
    )

    v186_block_audit.to_csv(
        OUT
        / "03_V187_V186固定跳過16筆.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # 影子結果，做為 V186 對照。
    control_with_key = (
        attach_key(
            control_trades
        )
    )

    v186_shadow = (
        control_with_key[
            ~control_with_key[
                "_key"
            ].isin(
                block_keys
            )
        ]
        .copy()
    )

    shadow_trades = len(
        v186_shadow
    )

    shadow_tp1 = int(
        pd.to_datetime(
            v186_shadow[
                "tp1_time_v92"
            ],
            utc=True,
            errors="coerce",
        ).notna().sum()
    )

    if (
        "已實現淨利_USDT"
        in v186_shadow.columns
    ):
        shadow_pnl = float(
            pd.to_numeric(
                v186_shadow[
                    "已實現淨利_USDT"
                ],
                errors="coerce",
            ).fillna(0).sum()
        )
    else:
        shadow_pnl = float(
            pd.to_numeric(
                v186_shadow[
                    "realized_profit_usdt_v92"
                ],
                errors="coerce",
            ).fillna(0).sum()
        )

    print(
        f"\nV157控制："
        f"{control_total['交易數']}筆｜"
        f"TP1 {control_total['TP1筆數']}｜"
        f"勝率 {control_total['總勝率_pct']:.2f}%｜"
        f"淨利 {control_total['三年淨利_U']:.4f}U"
    )

    print(
        f"V186影子重建："
        f"{shadow_trades}筆｜"
        f"TP1 {shadow_tp1}｜"
        f"勝率 {100*shadow_tp1/shadow_trades:.2f}%｜"
        f"逐筆U {shadow_pnl:.4f}"
    )

    print(
        f"V186固定跳過："
        f"{len(block_keys)}筆"
    )

    # -----------------------------------------------------------------
    # 2. 正式完整重排
    # -----------------------------------------------------------------

    module = load_module(
        base_script,
        "v187_base_engine",
    )

    if not hasattr(
        module,
        "load_v155",
    ):
        raise RuntimeError(
            "底層程式沒有 load_v155"
        )

    original_loader = (
        module.load_v155
    )

    def load_v155_patched():
        v155 = (
            original_loader()
        )

        # 先完整還原 V157。
        patch_v85_elite_rescue(
            v155
        )

        # 再套 V187 16筆固定防守跳過。
        patch_v187_fixed_blocks(
            v155,
            block_keys,
        )

        return v155

    module.load_v155 = (
        load_v155_patched
    )

    module.OUT = OUT
    module.BASELINE = control_result

    print(
        "\n" + "=" * 122
    )

    print(
        "開始 V187 正式三年完整重排："
        "MAX5 / NEW2 / cooldown / 動態1% / 成本 / 正式出場"
    )

    print(
        "=" * 122
    )

    code = int(
        module.main()
    )

    if code != 0:
        raise RuntimeError(
            f"底層 main 回傳 {code}"
        )

    # -----------------------------------------------------------------
    # 3. 驗收 block 是否真的全數生效
    # -----------------------------------------------------------------

    hit_count = len(
        BLOCK_HITS
    )

    if (
        hit_count
        != len(
            block_keys
        )
    ):
        missing = (
            block_keys
            - BLOCK_HITS
        )

        miss_df = pd.DataFrame(
            [
                {
                    "exchange": k[0],
                    "symbol": k[1],
                    "entry_time": k[2],
                }
                for k in sorted(
                    missing
                )
            ]
        )

        miss_df.to_csv(
            OUT
            / "V187_BLOCK未命中_錯誤.csv",
            index=False,
            encoding="utf-8-sig",
        )

        raise RuntimeError(
            f"V187固定16筆只有 {hit_count} 筆真正命中底層；"
            f"缺 {len(missing)} 筆。"
            "已輸出 V187_BLOCK未命中_錯誤.csv"
        )

    pd.DataFrame(
        BLOCK_AUDIT
    ).to_csv(
        OUT
        / "V187_底層BLOCK命中稽核.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # -----------------------------------------------------------------
    # 4. 結果
    # -----------------------------------------------------------------

    generated = (
        OUT
        / "V156_三年結果.csv"
    )

    if not generated.exists():
        candidates = [
            p
            for p in OUT.glob(
                "*三年結果.csv"
            )
            if p.is_file()
        ]

        if not candidates:
            raise FileNotFoundError(
                "找不到底層三年結果CSV"
            )

        generated = candidates[0]

    final_result = (
        OUT
        / "V187_三年結果.csv"
    )

    if generated.resolve() != final_result.resolve():
        shutil.copy2(
            generated,
            final_result,
        )

    final_summary = read_csv(
        final_result
    )

    final_total = totals(
        final_summary
    )

    compare = pd.DataFrame([
        {
            "版本": "V157_V162",
            **control_total,
            "相對V157淨利_U": 0.0,
            "相對V157勝率_百分點": 0.0,
            "相對V157回撤_百分點": 0.0,
        },
        {
            "版本": "V186_MAIN影子",
            "交易數": shadow_trades,
            "TP1筆數": shadow_tp1,
            "總勝率_pct": round(
                100.0
                * shadow_tp1
                / shadow_trades,
                4,
            ),
            "三年淨利_U": round(
                shadow_pnl,
                4,
            ),
            "1000U期末權益_U": round(
                START_EQUITY_U
                + shadow_pnl,
                4,
            ),
            "最大年度回撤_pct": math.nan,
            "相對V157淨利_U": round(
                shadow_pnl
                - control_total[
                    "三年淨利_U"
                ],
                4,
            ),
            "相對V157勝率_百分點": round(
                100.0
                * shadow_tp1
                / shadow_trades
                - control_total[
                    "總勝率_pct"
                ],
                4,
            ),
            "相對V157回撤_百分點": math.nan,
        },
        {
            "版本": "V187正式完整重排",
            **final_total,
            "相對V157淨利_U": round(
                final_total[
                    "三年淨利_U"
                ]
                - control_total[
                    "三年淨利_U"
                ],
                4,
            ),
            "相對V157勝率_百分點": round(
                final_total[
                    "總勝率_pct"
                ]
                - control_total[
                    "總勝率_pct"
                ],
                4,
            ),
            "相對V157回撤_百分點": round(
                final_total[
                    "最大年度回撤_pct"
                ]
                - control_total[
                    "最大年度回撤_pct"
                ],
                4,
            ),
        },
    ])

    compare.to_csv(
        OUT
        / "01_V187_V157_V186總比較.csv",
        index=False,
        encoding="utf-8-sig",
    )

    yearly = year_compare(
        control_summary,
        final_summary,
    )

    yearly.to_csv(
        OUT
        / "02_V187_逐年相對V157.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # -----------------------------------------------------------------
    # 5. 正式重排造成哪些新成交/消失成交
    # -----------------------------------------------------------------

    final_trades = (
        collect_full_trades(
            OUT
        )
    )

    added, removed = (
        compare_trade_sets(
            control_trades,
            final_trades,
        )
    )

    added.to_csv(
        OUT
        / "04_V187_重排後新增成交.csv",
        index=False,
        encoding="utf-8-sig",
    )

    removed.to_csv(
        OUT
        / "05_V187_相對V157消失成交.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # -----------------------------------------------------------------
    # 6. 2026自然月份
    # -----------------------------------------------------------------

    month_df = pd.concat(
        [
            monthly_2026(
                control_trades,
                "V157控制",
            ),
            monthly_2026(
                final_trades,
                "V187正式重排",
            ),
        ],
        ignore_index=True,
    )

    month_df.to_csv(
        OUT
        / "06_V187_2026每月勝率比較.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # -----------------------------------------------------------------
    # 7. 正式升級判定
    # -----------------------------------------------------------------

    pnl_ok = (
        final_total[
            "三年淨利_U"
        ]
        > control_total[
            "三年淨利_U"
        ]
    )

    win_ok = (
        final_total[
            "總勝率_pct"
        ]
        >= control_total[
            "總勝率_pct"
        ]
    )

    dd_ok = (
        final_total[
            "最大年度回撤_pct"
        ]
        <= control_total[
            "最大年度回撤_pct"
        ]
        + 1e-9
    )

    trade_ok = (
        final_total[
            "交易數"
        ]
        >= math.floor(
            control_total[
                "交易數"
            ]
            * 0.85
        )
    )

    nonnegative_years = int(
        (
            pd.to_numeric(
                yearly[
                    "淨利變化_U"
                ],
                errors="coerce",
            )
            >= 0
        ).sum()
    )

    year_ok = (
        nonnegative_years
        >= 2
    )

    validation_rows = (
        yearly[
            yearly[
                "期間"
            ].astype(
                str
            ).str.contains(
                "2025",
                na=False,
            )
        ]
    )

    if validation_rows.empty:
        validation_ok = False
        validation_delta = math.nan
    else:
        validation_delta = float(
            pd.to_numeric(
                validation_rows[
                    "淨利變化_U"
                ],
                errors="coerce",
            ).iloc[0]
        )

        validation_ok = (
            validation_delta
            >= 0
        )

    block_ok = (
        hit_count
        == EXPECTED_V186_BLOCKS
    )

    passed = (
        block_ok
        and pnl_ok
        and win_ok
        and dd_ok
        and trade_ok
        and year_ok
        and validation_ok
    )

    decision = {
        "版本": "V187",
        "16筆防守跳過全部命中底層": (
            "是"
            if block_ok
            else "否"
        ),
        "三年淨利高於V157": (
            "是"
            if pnl_ok
            else "否"
        ),
        "勝率不低於V157": (
            "是"
            if win_ok
            else "否"
        ),
        "最大年度回撤不惡化": (
            "是"
            if dd_ok
            else "否"
        ),
        "交易數至少保留85%": (
            "是"
            if trade_ok
            else "否"
        ),
        "至少2年相對V157不差": (
            "是"
            if year_ok
            else "否"
        ),
        "2025獨立驗證不差於V157": (
            "是"
            if validation_ok
            else "否"
        ),
        "2025相對V157淨利_U": (
            validation_delta
        ),
        "重排後新增成交數": len(
            added
        ),
        "相對V157消失成交數": len(
            removed
        ),
        "正式判定": (
            "PASS_可進V188實盤動態狀態機整合"
            if passed
            else "FAILED_OR_HOLD_維持V157並檢查重排差異"
        ),
    }

    pd.DataFrame(
        [decision]
    ).to_csv(
        OUT
        / "07_V187_正式升級判定.csv",
        index=False,
        encoding="utf-8-sig",
    )

    report = {
        "版本": (
            "V187｜V186四連敗防守固定決策｜三年正式完整重排"
        ),
        "固定規則": {
            "連敗觸發": LOSS_TRIGGER,
            "防守原V157正式成交機會": DEFENSE_ENTRIES,
            "最低4R機率": P4R_MIN,
            "固定跳過數": len(
                block_keys
            ),
        },
        "控制組": control_total,
        "V186影子重建": {
            "交易數": shadow_trades,
            "TP1": shadow_tp1,
            "勝率_pct": round(
                100.0
                * shadow_tp1
                / shadow_trades,
                4,
            ),
            "逐筆U": round(
                shadow_pnl,
                4,
            ),
        },
        "V187正式完整重排": final_total,
        "逐年": yearly.to_dict(
            "records"
        ),
        "正式重排影響": {
            "新增成交數": len(
                added
            ),
            "消失成交數": len(
                removed
            ),
            "底層BLOCK命中數": hit_count,
        },
        "升級判定": decision,
        "下一步": (
            "若PASS，V188才把四連敗/防守3筆/P4R20%做成"
            "真正實盤動態狀態機；V187本身不改動正式實盤程式。"
        ),
    }

    (
        OUT
        / "V187_最終報告.json"
    ).write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    # -----------------------------------------------------------------
    # 終端摘要
    # -----------------------------------------------------------------

    print(
        "\n" + "=" * 122
    )

    print(
        "V187｜總比較"
    )

    print(
        compare.to_string(
            index=False
        )
    )

    print(
        "\n逐年："
    )

    print(
        yearly.to_string(
            index=False
        )
    )

    print(
        "\n正式重排影響："
    )

    print(
        f"V186固定BLOCK命中："
        f"{hit_count}/{len(block_keys)}"
    )

    print(
        f"重排後新增成交：{len(added)}筆"
    )

    print(
        f"相對V157消失成交：{len(removed)}筆"
    )

    if not month_df.empty:
        print(
            "\n2026每月勝率（只到歷史資料尾端）："
        )

        print(
            month_df.to_string(
                index=False
            )
        )

    print(
        "\n正式升級判定："
    )

    print(
        pd.DataFrame(
            [decision]
        ).to_string(
            index=False
        )
    )

    print(
        f"\n輸出資料夾：{OUT}"
    )

    print(
        "=" * 122
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
