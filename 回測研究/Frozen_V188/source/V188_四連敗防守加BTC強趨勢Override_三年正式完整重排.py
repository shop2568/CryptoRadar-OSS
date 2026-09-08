#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V188｜V186四連敗防守 + BTC強趨勢Override｜三年正式完整重排
=============================================================

研究目的
--------
V187 正式完整重排結果：
- V157：220筆 / 77 TP1 / 35.00% / +3377.7408U / DD 17.0053%
- V187：207筆 / 76 TP1 / 36.7150% / +3359.1870U / DD 14.9891%
- 2023：+31.5530U 相對V157
- 2024：+54.9492U 相對V157
- 2025：-105.0560U 相對V157

=> 防守機制在較弱市況有效，但強趨勢會過度擋單。

V188 固定做法
-------------
1. 完整沿用 V186 MAIN：
   連續4筆已結束正式交易未命中TP1
   -> 接下來3個原V157正式成交機會進防守
   -> V185預測4R機率 <20% 原本應跳過。

2. 不改上面任何參數。

3. 對 V186 原本要跳過的16筆，再加入一個「BTC大盤強趨勢Override」：
   - 使用 V174 已經驗證過的 BTC ONLY COMBO2
   - 固定門檻：
       D1 ADX14 >=25 且 close>EMA20
       D1 close>EMA20>EMA50 且 EMA20五日斜率 >=0.20 ATR
       H4 close>EMA20>EMA50 且 EMA20六根斜率 >=0.20 ATR
       三項至少2項強 => BTC COMBO2 強
   - 全部只用 Entry 前已完成 D1 / H4 K。

4. 決策：
   - BTC COMBO2 強：
       KEEP V157（解除 V186 防守，不擋）
   - BTC COMBO2 不強：
       BLOCK（維持 V186 防守）
   - BTC資料不足：
       KEEP V157（資料不足不誤殺）

5. 再交回正式底層完整三年重排：
   MAX5 / NEW2 / cooldown / 動態1%資金 / 成本 / TP-SL / Runner 全部重跑。

重要
----
V188 仍是「固定決策研究版」：
四連敗/防守3筆是從原 V157 成交流重建；
BTC Override 是因果市場條件。

若 V188 正式重排 PASS，
V189 才把「四連敗 + BTC強勢解除防守」做成真正實盤動態狀態機。

禁止
----
- 不用年份作交易規則
- 不因2025結果改COMBO2門檻
- 不調4連敗
- 不調3筆防守
- 不調20%四R機率
- 不加ETH
"""

from __future__ import annotations

import importlib.util
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any

import pandas as pd


HOME = Path.home()
CRYPTO = HOME / "CryptoRadar"

V187_SCRIPT_NAME = "V187_四連敗防守固定決策_三年正式完整重排.py"
V174_SCRIPT_NAME = "V174_個幣COMBO2加BTCETH大盤市況分流_三年完整回測.py"

OUT = CRYPTO / "V188_四連敗防守加BTC強趨勢Override_三年正式完整重排"

START_EQUITY_U = 1000.0

EXPECTED_V186_BLOCKS = 16

V188_BLOCK_HITS: set[tuple[str, str, str]] = set()
V188_V186_SEEN: set[tuple[str, str, str]] = set()
V188_BLOCK_AUDIT: list[dict[str, Any]] = []


# =============================================================================
# 自動找程式
# =============================================================================

def find_file(name: str) -> Path | None:
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

    for root in (
        HOME / "Downloads",
        CRYPTO,
        HOME / "Desktop" / "回測專案",
        HOME / "Documents" / "ChatGPT" / "回測專案",
        HOME / "Documents" / "回測專案",
    ):
        if not root.exists():
            continue

        try:
            hits = list(root.rglob(name))
            if hits:
                return hits[0]
        except Exception:
            pass

    return None


def load_module(path: Path, name: str):
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


V187_PATH = find_file(
    V187_SCRIPT_NAME
)

if V187_PATH is None:
    raise FileNotFoundError(
        f"找不到 {V187_SCRIPT_NAME}\n"
        "請保留剛剛成功執行的 V187 程式在 Downloads。"
    )

V174_PATH = find_file(
    V174_SCRIPT_NAME
)

if V174_PATH is None:
    raise FileNotFoundError(
        f"找不到 {V174_SCRIPT_NAME}\n"
        "請保留先前成功執行的 V174 程式在 Downloads。"
    )

v187 = load_module(
    V187_PATH,
    "v188_v187_source",
)

v174 = load_module(
    V174_PATH,
    "v188_v174_source",
)


# =============================================================================
# V187正式結果
# =============================================================================

def find_v187_result() -> Path | None:
    direct = (
        CRYPTO
        / "V187_四連敗防守固定決策_三年正式完整重排"
        / "V187_三年結果.csv"
    )

    if direct.exists():
        return direct

    for root in (
        CRYPTO,
        HOME / "Downloads",
    ):
        if not root.exists():
            continue

        try:
            for p in root.rglob(
                "V187_三年結果.csv"
            ):
                return p
        except Exception:
            pass

    return None


# =============================================================================
# 16筆 V186 防守交易 + BTC COMBO2
# =============================================================================

def build_v188_market_decisions(
    base_script: Path,
    control_trades: pd.DataFrame,
) -> tuple[
    set[tuple[str, str, str]],
    set[tuple[str, str, str]],
    pd.DataFrame,
]:
    """
    回傳：
    - v186_all_keys：V186原本16筆
    - v188_block_keys：BTC不強，V188仍要擋的subset
    - audit
    """

    (
        v186_keys,
        v186_audit,
    ) = v187.derive_v186_blocks(
        control_trades
    )

    if len(v186_keys) != EXPECTED_V186_BLOCKS:
        raise RuntimeError(
            f"V186固定跳過重建 {len(v186_keys)} != 16"
        )

    market_loader = (
        v174.MarketFrameLoader(
            base_script
        )
    )

    block_keys: set[
        tuple[str, str, str]
    ] = set()

    rows = []

    for _, row in v186_audit.iterrows():
        year = str(
            row.get(
                "年度",
                "",
            )
        )

        signal_time = pd.to_datetime(
            row.get(
                "entry_time"
            ),
            utc=True,
            errors="coerce",
        )

        key = v187.trade_key(
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

        btc = v174.market_combo2(
            market_loader=market_loader,
            year=year,
            signal_time=signal_time,
            asset="BTC",
        )

        # 完全沿用 V174 BTC_ONLY 的保守原則：
        # BTC資料不足 -> 維持V157，不誤殺。
        if not btc.get(
            "ok",
            False,
        ):
            action = (
                "KEEP_V157_BTC資料不足"
            )
            override = True

        elif btc.get(
            "strong",
            False,
        ):
            action = (
                "KEEP_V157_BTC_COMBO2強"
            )
            override = True

        else:
            action = (
                "BLOCK_V186防守_BTC不強"
            )
            override = False
            block_keys.add(
                key
            )

        rows.append({
            **row.to_dict(),
            "BTC_COMBO2可判斷": (
                "是"
                if btc.get(
                    "ok",
                    False,
                )
                else "否"
            ),
            "BTC_COMBO2強": (
                "是"
                if btc.get(
                    "strong",
                    False,
                )
                else "否"
            ),
            "BTC強條件數": btc.get(
                "strong_count"
            ),
            "BTC_D1_ADX14": btc.get(
                "D1_ADX14"
            ),
            "BTC_D1_EMA20斜率_ATR": btc.get(
                "D1_EMA20斜率_ATR"
            ),
            "BTC_H4_EMA20斜率_ATR": btc.get(
                "H4_EMA20斜率_ATR"
            ),
            "BTC判定原因": btc.get(
                "reason"
            ),
            "V188大盤Override": (
                "是"
                if override
                else "否"
            ),
            "V188最終動作": action,
            "V188Key": str(
                key
            ),
        })

    audit = pd.DataFrame(
        rows
    )

    return (
        v186_keys,
        block_keys,
        audit,
    )


# =============================================================================
# V188 patch
# =============================================================================

def patch_v188(
    v155,
    all_v186_keys: set[
        tuple[str, str, str]
    ],
    v188_block_keys: set[
        tuple[str, str, str]
    ],
) -> None:
    original_transform = (
        v155.transform_v154
    )

    def transform_v188(
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

        key = v187.trade_key(
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

        if key not in all_v186_keys:
            return (
                transformed,
                audit,
            )

        # 確認原V186的16筆全部有走到這裡。
        V188_V186_SEEN.add(
            key
        )

        # BTC強 / 資料不足：
        # 不block，維持V157。
        if key not in v188_block_keys:
            audit[
                "V188防守模式"
            ] = (
                "KEEP_V157_BTC強勢Override"
            )

            return (
                transformed,
                audit,
            )

        # BTC不強：
        # 維持V186防守，正式block。
        V188_BLOCK_HITS.add(
            key
        )

        V188_BLOCK_AUDIT.append({
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
            "Entry時間": transformed.get(
                "entry_time"
            ),
            "Entry": transformed.get(
                "entry"
            ),
            "Stop": transformed.get(
                "stop"
            ),
            "V188動作": (
                "BLOCK_V186防守_BTC_COMBO2不強"
            ),
            "Key": str(
                key
            ),
        })

        audit[
            "keep"
        ] = False

        audit[
            "V188防守模式"
        ] = (
            "BLOCK_V186防守_BTC_COMBO2不強"
        )

        audit[
            "reason"
        ] = (
            "BLOCK_V188｜"
            "V186四連敗防守成立且BTC COMBO2不強"
        )

        return (
            None,
            audit,
        )

    v155.transform_v154 = (
        transform_v188
    )


# =============================================================================
# 統計輔助
# =============================================================================

def effect_summary(
    audit: pd.DataFrame,
) -> pd.DataFrame:
    if audit.empty:
        return pd.DataFrame()

    return (
        audit.groupby(
            [
                "年度",
                "V188最終動作",
            ],
            dropna=False,
        )
        .size()
        .reset_index(
            name="筆數"
        )
        .sort_values(
            [
                "年度",
                "V188最終動作",
            ]
        )
    )


# =============================================================================
# 主程式
# =============================================================================

def main() -> int:
    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(
        "=" * 124
    )

    print(
        "V188｜四連敗防守 + BTC強趨勢Override｜三年正式完整重排"
    )

    print(
        "固定V186：4連敗 / 防守3筆 / P4R<20%"
    )

    print(
        "新增固定Override：BTC V174-COMBO2強 -> 維持V157；BTC弱 -> 才防守"
    )

    print(
        "=" * 124
    )

    base_script = (
        v187.find_base_script()
    )

    control_result = (
        v187.find_control_result()
    )

    v185_zip = (
        v187.find_v185_zip()
    )

    control_summary = (
        v187.read_csv(
            control_result
        )
    )

    control_total = (
        v187.totals(
            control_summary
        )
    )

    control_trades = (
        v187.load_v185a_control_trades(
            v185_zip
        )
    )

    (
        all_v186_keys,
        v188_block_keys,
        market_audit,
    ) = build_v188_market_decisions(
        base_script=base_script,
        control_trades=control_trades,
    )

    market_audit.to_csv(
        OUT
        / "03_V188_V186原16筆_BTC市況逐筆稽核.csv",
        index=False,
        encoding="utf-8-sig",
    )

    effects = effect_summary(
        market_audit
    )

    effects.to_csv(
        OUT
        / "04_V188_BTC_Override生效統計.csv",
        index=False,
        encoding="utf-8-sig",
    )

    override_count = int(
        (
            market_audit[
                "V188大盤Override"
            ]
            .astype(str)
            .eq("是")
        ).sum()
    )

    block_count = len(
        v188_block_keys
    )

    data_missing_count = int(
        (
            market_audit[
                "V188最終動作"
            ]
            .astype(str)
            .eq(
                "KEEP_V157_BTC資料不足"
            )
        ).sum()
    )

    print(
        "\nV186原16筆重新分類："
    )

    print(
        f"BTC強/資料不足 Override維持V157："
        f"{override_count}筆"
    )

    print(
        f"BTC不強，V188仍正式防守："
        f"{block_count}筆"
    )

    print(
        f"其中BTC資料不足："
        f"{data_missing_count}筆"
    )

    if not effects.empty:
        print(
            "\n逐年分流："
        )

        print(
            effects.to_string(
                index=False
            )
        )

    # -----------------------------------------------------------------
    # 正式完整重排
    # -----------------------------------------------------------------

    module = load_module(
        base_script,
        "v188_base_engine",
    )

    if not hasattr(
        module,
        "load_v155",
    ):
        raise RuntimeError(
            "底層缺少 load_v155"
        )

    original_loader = (
        module.load_v155
    )

    def load_v155_patched():
        v155 = (
            original_loader()
        )

        # 先完整還原V157
        v187.patch_v85_elite_rescue(
            v155
        )

        # 再套V188
        patch_v188(
            v155=v155,
            all_v186_keys=all_v186_keys,
            v188_block_keys=v188_block_keys,
        )

        return v155

    module.load_v155 = (
        load_v155_patched
    )

    module.OUT = OUT
    module.BASELINE = (
        control_result
    )

    print(
        "\n" + "=" * 124
    )

    print(
        "開始正式重排：MAX5 / NEW2 / cooldown / 動態1% / 替補交易 / 成本 / 正式出場"
    )

    print(
        "=" * 124
    )

    code = int(
        module.main()
    )

    if code != 0:
        raise RuntimeError(
            f"底層 main 回傳 {code}"
        )

    # -----------------------------------------------------------------
    # 強制稽核
    # -----------------------------------------------------------------

    if len(
        V188_V186_SEEN
    ) != len(
        all_v186_keys
    ):
        missing = (
            all_v186_keys
            - V188_V186_SEEN
        )

        pd.DataFrame(
            [
                {
                    "exchange": x[0],
                    "symbol": x[1],
                    "entry_time": x[2],
                }
                for x in sorted(
                    missing
                )
            ]
        ).to_csv(
            OUT
            / "V188_V186原16筆未走到底層_錯誤.csv",
            index=False,
            encoding="utf-8-sig",
        )

        raise RuntimeError(
            "V188 原V186的16筆沒有全部走到底層；"
            f"只看到 {len(V188_V186_SEEN)}/16"
        )

    if len(
        V188_BLOCK_HITS
    ) != len(
        v188_block_keys
    ):
        missing = (
            v188_block_keys
            - V188_BLOCK_HITS
        )

        pd.DataFrame(
            [
                {
                    "exchange": x[0],
                    "symbol": x[1],
                    "entry_time": x[2],
                }
                for x in sorted(
                    missing
                )
            ]
        ).to_csv(
            OUT
            / "V188_應BLOCK未命中_錯誤.csv",
            index=False,
            encoding="utf-8-sig",
        )

        raise RuntimeError(
            f"V188應BLOCK {len(v188_block_keys)}筆，"
            f"實際只命中 {len(V188_BLOCK_HITS)}筆"
        )

    pd.DataFrame(
        V188_BLOCK_AUDIT
    ).to_csv(
        OUT
        / "05_V188_底層正式BLOCK命中.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # -----------------------------------------------------------------
    # 三年結果
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
        / "V188_三年結果.csv"
    )

    if (
        generated.resolve()
        != final_result.resolve()
    ):
        shutil.copy2(
            generated,
            final_result,
        )

    final_summary = (
        v187.read_csv(
            final_result
        )
    )

    final_total = (
        v187.totals(
            final_summary
        )
    )

    # V187 reference
    v187_result = (
        find_v187_result()
    )

    if v187_result is not None:
        v187_summary = (
            v187.read_csv(
                v187_result
            )
        )

        v187_total = (
            v187.totals(
                v187_summary
            )
        )
    else:
        v187_summary = None
        v187_total = None

    compare_rows = [
        {
            "版本": "V157_V162",
            **control_total,
            "相對V157淨利_U": 0.0,
            "相對V157勝率_百分點": 0.0,
            "相對V157回撤_百分點": 0.0,
        },
    ]

    if v187_total is not None:
        compare_rows.append({
            "版本": "V187四連敗防守",
            **v187_total,
            "相對V157淨利_U": round(
                v187_total[
                    "三年淨利_U"
                ]
                - control_total[
                    "三年淨利_U"
                ],
                4,
            ),
            "相對V157勝率_百分點": round(
                v187_total[
                    "總勝率_pct"
                ]
                - control_total[
                    "總勝率_pct"
                ],
                4,
            ),
            "相對V157回撤_百分點": round(
                v187_total[
                    "最大年度回撤_pct"
                ]
                - control_total[
                    "最大年度回撤_pct"
                ],
                4,
            ),
        })

    compare_rows.append({
        "版本": "V188防守+BTC強勢Override",
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
    })

    compare = pd.DataFrame(
        compare_rows
    )

    compare.to_csv(
        OUT
        / "01_V188_V157_V187總比較.csv",
        index=False,
        encoding="utf-8-sig",
    )

    yearly = (
        v187.year_compare(
            control_summary,
            final_summary,
        )
    )

    yearly.to_csv(
        OUT
        / "02_V188_逐年相對V157.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # -----------------------------------------------------------------
    # 重排差異
    # -----------------------------------------------------------------

    final_trades = (
        v187.collect_full_trades(
            OUT
        )
    )

    (
        added,
        removed,
    ) = v187.compare_trade_sets(
        control_trades,
        final_trades,
    )

    added.to_csv(
        OUT
        / "06_V188_重排後新增成交.csv",
        index=False,
        encoding="utf-8-sig",
    )

    removed.to_csv(
        OUT
        / "07_V188_相對V157消失成交.csv",
        index=False,
        encoding="utf-8-sig",
    )

    months = pd.concat(
        [
            v187.monthly_2026(
                control_trades,
                "V157控制",
            ),
            v187.monthly_2026(
                final_trades,
                "V188正式重排",
            ),
        ],
        ignore_index=True,
    )

    months.to_csv(
        OUT
        / "08_V188_2026每月勝率比較.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # -----------------------------------------------------------------
    # 升級判定
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

    validation = yearly[
        yearly[
            "期間"
        ]
        .astype(str)
        .str.contains(
            "2025",
            na=False,
        )
    ]

    if validation.empty:
        validation_delta = math.nan
        validation_ok = False
    else:
        validation_delta = float(
            pd.to_numeric(
                validation[
                    "淨利變化_U"
                ],
                errors="coerce",
            ).iloc[0]
        )

        validation_ok = (
            validation_delta
            >= 0
        )

    decision = {
        "版本": "V188",
        "V186原16筆全部走到底層": (
            "是"
            if len(
                V188_V186_SEEN
            )
            == 16
            else "否"
        ),
        "BTC強勢Override筆數": (
            override_count
        ),
        "BTC弱正式防守筆數": (
            block_count
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
    }

    passed = (
        pnl_ok
        and win_ok
        and dd_ok
        and trade_ok
        and year_ok
        and validation_ok
        and len(
            V188_V186_SEEN
        )
        == 16
        and len(
            V188_BLOCK_HITS
        )
        == len(
            v188_block_keys
        )
    )

    decision[
        "正式判定"
    ] = (
        "PASS_可進V189實盤動態狀態機整合"
        if passed
        else "FAILED_OR_HOLD_維持V157並檢查BTC_Override逐筆"
    )

    pd.DataFrame(
        [decision]
    ).to_csv(
        OUT
        / "09_V188_正式升級判定.csv",
        index=False,
        encoding="utf-8-sig",
    )

    report = {
        "版本": (
            "V188｜四連敗防守 + BTC強趨勢Override｜正式完整重排"
        ),
        "固定規則": {
            "V186": (
                "4連敗 -> 防守3個原V157正式成交機會 -> P4R<20%"
            ),
            "BTC_Override": (
                "BTC V174 COMBO2強 -> 維持V157；"
                "BTC不強 -> 維持V186防守；"
                "資料不足 -> 維持V157"
            ),
            "COMBO2門檻": (
                "V174原版固定 25 / 0.20 / 0.20，三項至少2強"
            ),
        },
        "V186原跳過": 16,
        "V188市場分流": {
            "Override維持V157": override_count,
            "BTC弱仍Block": block_count,
            "BTC資料不足": data_missing_count,
        },
        "V157": control_total,
        "V187": v187_total,
        "V188": final_total,
        "逐年": yearly.to_dict(
            "records"
        ),
        "正式重排": {
            "新增成交": len(
                added
            ),
            "消失成交": len(
                removed
            ),
        },
        "升級判定": decision,
        "下一步": (
            "只有V188正式PASS，V189才把四連敗狀態+BTC COMBO2"
            "改成真正實盤動態狀態機。"
        ),
    }

    (
        OUT
        / "V188_最終報告.json"
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
    # 終端
    # -----------------------------------------------------------------

    print(
        "\n" + "=" * 124
    )

    print(
        "V188｜總比較"
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
        "\nBTC Override："
    )

    print(
        f"原V186 16筆｜"
        f"BTC強/資料不足恢復 {override_count}筆｜"
        f"BTC弱仍擋 {block_count}筆"
    )

    print(
        f"底層原16筆Seen："
        f"{len(V188_V186_SEEN)}/16"
    )

    print(
        f"正式BLOCK命中："
        f"{len(V188_BLOCK_HITS)}/{block_count}"
    )

    print(
        f"重排新增成交：{len(added)}筆｜"
        f"消失成交：{len(removed)}筆"
    )

    if not months.empty:
        print(
            "\n2026每月勝率（只到歷史資料尾端）："
        )

        print(
            months.to_string(
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
        "=" * 124
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
