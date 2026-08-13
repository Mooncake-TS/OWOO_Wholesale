"""사용자 제공 샘플 파일로 데이터 파이프라인을 확인하는 간단한 실행 테스트."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from data_pipeline import (
    _wholesale_warehouse_mask,
    add_product_classification,
    build_inventory_group_summary,
    build_purchase_decision,
    load_inventory,
    load_purchases,
    load_sales,
    monthly_comparison,
    same_period_frames,
)


SAMPLE_DIR = Path(os.environ.get("ERP_SAMPLE_DIR", "sample_data"))


def main() -> None:
    required_files = [
        "25~26 총판 매장출고반품현황_작업용.xlsx",
        "25~26 총판 상품입고반품현황_작업용.xlsx",
        "26년1월1일 총판 창고재고현황.xlsx",
    ]
    missing_files = [name for name in required_files if not (SAMPLE_DIR / name).exists()]
    if missing_files:
        raise SystemExit(
            "샘플 테스트를 실행하려면 ERP_SAMPLE_DIR 환경변수에 샘플 엑셀 폴더를 "
            f"지정해 주세요. 누락 파일: {', '.join(missing_files)}"
        )

    code_examples = pd.DataFrame(
        {
            "상품코드": [
                "A1AC1AC01",
                "A2AC1AC01",
                "A3AC1AC01",
                "AYAC1AC01",
                "AVAC1AC01",
                "AZAC1AC01",
                "A1AB1AC01",
                "A1AC2AC01",
                "BYAL1CO02",
            ]
        }
    )
    classified_examples = add_product_classification(code_examples)
    assert classified_examples.loc[0:5, "통합상품키"].nunique() == 1
    assert classified_examples.loc[0, "통합상품키"] == "A|AC|1|AC01"
    assert classified_examples.loc[0:2, "생산연도"].tolist() == ["2021", "2022", "2023"]
    assert classified_examples.loc[3:5, "생산연도"].eq("기타연도").all()
    assert classified_examples.loc[6, "통합상품키"] != classified_examples.loc[0, "통합상품키"]
    assert classified_examples.loc[7, "통합상품키"] != classified_examples.loc[0, "통합상품키"]
    assert not bool(classified_examples.loc[8, "아레나분류가능"])

    warehouse_examples = pd.DataFrame(
        {"창고명": ["총판", "테마", "온라인", " 총판 ", None]}
    )
    assert _wholesale_warehouse_mask(warehouse_examples, "창고명").tolist() == [
        True,
        False,
        False,
        True,
        False,
    ]

    sales, sales_meta = load_sales(SAMPLE_DIR / "25~26 총판 매장출고반품현황_작업용.xlsx")
    purchases, purchase_meta = load_purchases(SAMPLE_DIR / "25~26 총판 상품입고반품현황_작업용.xlsx")
    inventory, inventory_meta = load_inventory(SAMPLE_DIR / "26년1월1일 총판 창고재고현황.xlsx")
    group_summary = build_inventory_group_summary(inventory)
    decision = build_purchase_decision(inventory, sales, purchases=purchases)
    inventory_only_decision = build_purchase_decision(inventory, pd.DataFrame())
    decision_2026 = build_purchase_decision(
        inventory,
        sales,
        purchases=purchases,
        analysis_year=2026,
        start_month=2,
        end_month=6,
    )
    legacy_cache_decision = build_purchase_decision(
        inventory.drop(columns=["생산연도정렬"]),
        sales.drop(columns=["생산연도정렬"]),
        purchases=purchases.drop(columns=["생산연도정렬"]),
        analysis_year=2026,
        start_month=2,
        end_month=6,
    )
    sales_2025, sales_2024, _ = same_period_frames(
        sales, 2025, start_month=2, end_month=6
    )
    monthly_sales = monthly_comparison(
        sales,
        2025,
        quantity_column="순매출수량",
        amount_column="순매출금액",
        start_month=2,
        end_month=6,
    )

    assert sales_meta.clean_rows == len(sales)
    assert purchase_meta.clean_rows == len(purchases)
    assert inventory_meta.clean_rows == len(inventory)
    assert sales["창고"].astype("string").str.strip().eq("총판").all()
    assert purchases["창고명"].astype("string").str.strip().eq("총판").all()
    assert inventory["창고명"].astype("string").str.strip().eq("총판").all()
    assert sales["분석일자"].notna().all()
    assert purchases["분석일자"].notna().all()
    assert not decision.empty
    assert {"현재재고", "분석기간판매", "제안발주수량", "구매판단"}.issubset(decision.columns)
    assert decision["통합상품키"].str.startswith("A|").all()
    assert not group_summary.empty
    assert not inventory_only_decision.empty
    assert inventory_only_decision["분석기간판매"].eq(0).all()
    assert inventory_only_decision["제안발주수량"].eq(0).all()
    assert decision_2026["제안발주품번"].str.startswith("A6").all()
    assert not legacy_cache_decision.empty
    assert legacy_cache_decision["제안발주품번"].str.startswith("A6").all()
    assert sales_2025["월"].between(2, 6).all()
    assert sales_2024.empty or sales_2024["월"].between(2, 6).all()
    assert monthly_sales["월"].tolist() == [2, 3, 4, 5, 6]
    assert {"수량 차이", "수량 변화율", "금액 차이", "금액 변화율"}.issubset(
        monthly_sales.columns
    )
    assert (
        monthly_sales["수량 차이"]
        == monthly_sales["2025 수량"] - monthly_sales["2024 수량"]
    ).all()
    assert (
        monthly_sales["금액 차이"]
        == monthly_sales["2025 금액"] - monthly_sales["2024 금액"]
    ).all()

    print(f"sales={sales_meta}")
    print(f"purchases={purchase_meta}")
    print(f"inventory={inventory_meta}")
    print(
        f"arena_classification={inventory['아레나분류가능'].mean() * 100:.1f}% / "
        f"groups={len(group_summary):,}"
    )
    print(f"decision_rows={len(decision):,}")


if __name__ == "__main__":
    main()
