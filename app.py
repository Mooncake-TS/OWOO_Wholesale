"""총판 ERP 매출·매입·재고 분석 대시보드의 1차 골격."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from io import BytesIO

import altair as alt
import pandas as pd
import streamlit as st

from data_pipeline import (
    LoadMeta,
    SchemaError,
    build_inventory_group_summary,
    build_purchase_decision,
    load_inventory,
    load_purchases,
    load_sales,
    monthly_comparison,
    same_period_frames,
)


st.set_page_config(
    page_title="총판 ERP 분석 대시보드",
    page_icon="📦",
    layout="wide",
)


@st.cache_data(show_spinner=False)
def _load_uploaded(file_bytes: bytes, kind: str):
    source = BytesIO(file_bytes)
    if kind == "sales":
        return load_sales(source)
    if kind == "purchases":
        return load_purchases(source)
    if kind == "inventory":
        return load_inventory(source)
    raise ValueError(f"지원하지 않는 파일 종류: {kind}")


def _read_upload(uploaded_file, kind: str):
    if uploaded_file is None:
        return None, None, None
    try:
        frame, meta = _load_uploaded(uploaded_file.getvalue(), kind)
        return frame, meta, None
    except (SchemaError, ValueError, KeyError) as error:
        return None, None, str(error)
    except Exception as error:  # 업로드 파일 손상 등 예측하기 어려운 오류를 화면에 안내
        return None, None, f"파일을 읽는 중 오류가 발생했습니다: {error}"


def _format_number(value: float) -> str:
    return f"{value:,.0f}"


def _format_won(value: float) -> str:
    return f"₩{value:,.0f}"


def _format_change(current: float, previous: float) -> str:
    if previous == 0:
        return "비교 기준 없음"
    return f"{(current / previous - 1) * 100:+.1f}%"


def _comparison_chart(
    monthly: pd.DataFrame,
    columns: list[str],
    value_label: str,
) -> alt.Chart:
    chart_data = (
        monthly.reset_index()
        .melt(
            id_vars="월",
            value_vars=columns,
            var_name="구분",
            value_name=value_label,
        )
    )
    return (
        alt.Chart(chart_data)
        .mark_line(point=True)
        .encode(
            x=alt.X(
                "월:O",
                title="월",
                sort=list(range(1, 13)),
                axis=alt.Axis(labelExpr="datum.label + '월'"),
            ),
            y=alt.Y(
                f"{value_label}:Q",
                title=value_label,
                axis=alt.Axis(format=",.0f"),
            ),
            color=alt.Color("구분:N", title=None),
            tooltip=[
                alt.Tooltip("월:O", title="월"),
                alt.Tooltip("구분:N", title="구분"),
                alt.Tooltip(f"{value_label}:Q", title=value_label, format=",.0f"),
            ],
        )
        .properties(height=320)
    )


def _meta_caption(meta: LoadMeta) -> str:
    parts = [f"정제 {meta.clean_rows:,}행", f"제외 {meta.dropped_rows:,}행", f"헤더 {meta.header_row}행"]
    if meta.date_min and meta.date_max:
        parts.append(f"기간 {meta.date_min} ~ {meta.date_max}")
    if meta.snapshot_date:
        parts.append(f"재고 기준일 {meta.snapshot_date}")
    return " · ".join(parts)


def _source_card(title: str, meta: LoadMeta | None, error: str | None) -> None:
    st.markdown(f"**{title}**")
    if error:
        st.error(error)
    elif meta:
        st.success("파일 인식 완료")
        st.caption(_meta_caption(meta))
    else:
        st.info("파일을 업로드해 주세요.")


def _available_years(*frames: pd.DataFrame | None) -> list[int]:
    years: set[int] = set()
    for frame in frames:
        if frame is not None and not frame.empty and "연도" in frame.columns:
            years.update(int(year) for year in frame["연도"].dropna().unique())
    return sorted(years)


def _comparison_tab(
    frame: pd.DataFrame | None,
    analysis_year: int | None,
    start_month: int,
    end_month: int,
    quantity_column: str,
    amount_column: str,
    label: str,
) -> None:
    if frame is None:
        st.info(f"{label} 파일을 업로드하면 전년 동기간 비교가 표시됩니다.")
        return
    if analysis_year is None:
        st.warning("분석 가능한 연도가 없습니다.")
        return

    current, previous, _cutoff = same_period_frames(
        frame,
        analysis_year,
        start_month=start_month,
        end_month=end_month,
    )
    current_quantity = float(current[quantity_column].sum())
    previous_quantity = float(previous[quantity_column].sum())
    current_amount = float(current[amount_column].sum())
    previous_amount = float(previous[amount_column].sum())

    st.caption(
        f"{analysis_year}년 {start_month}~{end_month}월과 "
        f"{analysis_year - 1}년 동일 기간을 비교합니다."
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric(f"{analysis_year} 순{label} 수량", _format_number(current_quantity), _format_change(current_quantity, previous_quantity))
    col2.metric(f"{analysis_year - 1} 순{label} 수량", _format_number(previous_quantity))
    col3.metric(f"{analysis_year} 순{label} 금액", _format_won(current_amount), _format_change(current_amount, previous_amount))
    col4.metric(f"{analysis_year - 1} 순{label} 금액", _format_won(previous_amount))

    monthly = monthly_comparison(
        frame,
        analysis_year,
        quantity_column=quantity_column,
        amount_column=amount_column,
        start_month=start_month,
        end_month=end_month,
    ).set_index("월")

    quantity_columns = [f"{analysis_year - 1} 수량", f"{analysis_year} 수량"]
    amount_columns = [f"{analysis_year - 1} 금액", f"{analysis_year} 금액"]
    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.subheader("월별 수량 추이")
        st.altair_chart(
            _comparison_chart(monthly, quantity_columns, "수량"),
            width="stretch",
        )
    with chart_col2:
        st.subheader("월별 금액 추이")
        st.altair_chart(
            _comparison_chart(monthly, amount_columns, "금액"),
            width="stretch",
        )

    with st.expander("월별 비교표 보기"):
        st.dataframe(
            monthly.style.format("{:,.0f}"),
            width="stretch",
        )


st.title("총판 ERP 분석 대시보드")
st.caption("ERP 원본의 컬럼을 확인하고 분석으로 연결하는 1차 대시보드 골격입니다.")

with st.sidebar:
    st.header("ERP 파일 업로드")
    sales_file = st.file_uploader(
        "매장 출고·반품",
        type=["xlsx"],
        help="매장명, 구분, 일자, 상품코드, 수량, 출고가금액 컬럼이 있는 파일",
    )
    purchase_file = st.file_uploader(
        "상품 입고·반품",
        type=["xlsx"],
        help="조회구분, 조회일자, 상품코드, 총수량, 입고금액 컬럼이 있는 파일",
    )
    inventory_file = st.file_uploader(
        "ERP 현재 재고",
        type=["xlsx"],
        help="상품코드, 칼라, 사이즈, 수량, 원가 합계 컬럼이 있는 현재 재고 파일",
    )
    st.divider()
    target_months = st.slider("목표 재고 보유 개월", 1.0, 6.0, 3.0, 0.5)
    st.caption("발주 제안은 아레나 통합상품군 재고를 반영하며, 리드타임과 미입고 발주는 다음 단계에서 반영합니다.")

sales, sales_meta, sales_error = _read_upload(sales_file, "sales")
purchases, purchases_meta, purchases_error = _read_upload(purchase_file, "purchases")
inventory, inventory_meta, inventory_error = _read_upload(inventory_file, "inventory")

years = _available_years(sales, purchases)
purchase_year = date.today().year
year_options = sorted(set(years + [purchase_year]))
filter1, filter2, filter3 = st.columns(3)
with filter1:
    analysis_year = st.selectbox(
        "비교 분석연도", year_options, index=len(year_options) - 1
    )
with filter2:
    start_month = st.selectbox(
        "시작월", list(range(1, 13)), index=0, format_func=lambda month: f"{month}월"
    )
with filter3:
    end_month = st.selectbox(
        "종료월",
        list(range(start_month, 13)),
        index=12 - start_month,
        format_func=lambda month: f"{month}월",
    )
st.caption(
    f"매출·매입 비교는 선택한 분석연도를 사용하고, 구매판단은 {purchase_year}년 판매와 "
    f"A{purchase_year % 10} 발주 품번으로 고정합니다."
)

status_tab, sales_tab, purchase_tab, inventory_tab, mapping_tab = st.tabs(
    ["데이터 상태", "매출 비교", "매입 비교", "상품군 재고·구매판단", "상품코드 분류"]
)

with status_tab:
    st.subheader("업로드 및 정제 상태")
    card1, card2, card3 = st.columns(3)
    with card1:
        _source_card("매장 출고·반품", sales_meta, sales_error)
    with card2:
        _source_card("상품 입고·반품", purchases_meta, purchases_error)
    with card3:
        _source_card("ERP 현재 재고", inventory_meta, inventory_error)

    st.markdown("#### 적용 중인 기본 정제 규칙")
    st.markdown(
        "- 날짜가 아니거나 거래 구분이 올바르지 않은 소계·합계 행 제외\n"
        "- 반품 수량과 금액은 음수, 출고·입고는 양수로 표준화\n"
        "- 현재 재고는 계산하지 않고 ERP 재고 파일의 수량을 직접 사용\n"
        "- 아레나 9자리 품번은 생산연도 자리를 제거해 통합상품군으로 자동 분류\n"
        "- 최종 발주판단 단위는 `통합상품군 + 칼라 + 사이즈` 사용"
    )

    if sales_meta or purchases_meta or inventory_meta:
        with st.expander("정제 메타데이터"):
            metadata = [
                asdict(meta)
                for meta in (sales_meta, purchases_meta, inventory_meta)
                if meta is not None
            ]
            st.dataframe(pd.DataFrame(metadata), width="stretch")

with sales_tab:
    st.subheader("전년 대비 매출")
    _comparison_tab(
        sales,
        analysis_year,
        start_month,
        end_month,
        "순매출수량",
        "순매출금액",
        "매출",
    )

with purchase_tab:
    st.subheader("전년 대비 매입")
    _comparison_tab(
        purchases,
        analysis_year,
        start_month,
        end_month,
        "순매입수량",
        "순매입금액",
        "매입",
    )

with inventory_tab:
    st.subheader("핵심품번별 재고 상세")
    if inventory is None:
        st.info("ERP 현재 재고 파일을 업로드해 주세요.")
    else:
        classified_inventory = inventory.loc[inventory["아레나분류가능"]].copy()
        group_summary = build_inventory_group_summary(inventory)
        decision = build_purchase_decision(
            inventory,
            sales if sales is not None else pd.DataFrame(),
            purchases=purchases,
            target_months=target_months,
            analysis_year=purchase_year,
            start_month=start_month,
            end_month=end_month,
        )

        st.caption(
            "AC01처럼 핵심품번을 선택하면 생산연도만 다른 실제 품번의 재고를 "
            f"한 번에 합산해 보여줍니다. 판매 기준기간: {purchase_year}년 "
            f"{start_month}~{end_month}월."
        )

        if group_summary.empty:
            st.warning("자동 분류 가능한 아레나 9자리 품번이 없습니다.")
        else:
            group_options = group_summary["통합상품키"].tolist()
            duplicate_codes = group_summary["상품군표시"].value_counts()
            group_labels = {}
            for _, row in group_summary.iterrows():
                core_code = row["상품군표시"]
                if duplicate_codes.get(core_code, 0) > 1:
                    group_labels[row["통합상품키"]] = (
                        f"{core_code} · {row['대표상품명']}"
                    )
                else:
                    group_labels[row["통합상품키"]] = core_code
            default_index = next(
                (
                    index
                    for index, key in enumerate(group_options)
                    if group_labels[key] == "AC01"
                    or group_labels[key].startswith("AC01 ·")
                ),
                0,
            )
            selected_group_key = st.selectbox(
                "핵심품번 선택 (검색 가능)",
                group_options,
                index=default_index,
                format_func=lambda key: group_labels[key],
            )
            selected_inventory = classified_inventory.loc[
                classified_inventory["통합상품키"].eq(selected_group_key)
            ].copy()
            selected_decision = decision.loc[
                decision["통합상품키"].eq(selected_group_key)
            ].copy()
            sku_code_stock = (
                selected_inventory.groupby(
                    ["통합SKU키", "상품코드"], dropna=False
                )["현재재고수량"]
                .sum()
                .reset_index(name="품번현재재고")
            )
            sku_code_stock["품번재고표시"] = sku_code_stock.apply(
                lambda row: f"{row['상품코드']}: {row['품번현재재고']:,.0f}개",
                axis=1,
            )
            sku_stock_labels = (
                sku_code_stock.groupby("통합SKU키", dropna=False)["품번재고표시"]
                .agg(" / ".join)
                .rename("연결품번별 재고")
                .reset_index()
            )
            selected_decision_detail = selected_decision.merge(
                sku_stock_labels, on="통합SKU키", how="left"
            )
            selected_name = selected_inventory["상품군표시"].iloc[0]

            st.markdown(f"### {selected_name} 재고 상세")
            detail1, detail2, detail3, detail4 = st.columns(4)
            detail1.metric(
                f"{selected_name} 전체 재고",
                _format_number(float(selected_inventory["현재재고수량"].sum())),
            )
            detail2.metric(
                "재고원가",
                _format_won(float(selected_inventory["현재재고원가"].sum())),
            )
            detail3.metric(
                "생산연도 수", f"{selected_inventory['생산연도'].nunique():,}"
            )
            detail4.metric(
                "실제 품번 수", f"{selected_inventory['상품코드'].nunique():,}"
            )
            included_codes = (
                selected_inventory[["생산연도", "상품코드"]]
                .drop_duplicates()
                .sort_values(["생산연도", "상품코드"])["상품코드"]
                .tolist()
            )
            st.caption(f"포함 품번: {', '.join(included_codes)}")

            code_year_tab, color_tab, order_tab = st.tabs(
                ["품번·생산연도별", "컬러·사이즈별", "발주 판단"]
            )
            with code_year_tab:
                code_year_view = (
                    selected_inventory.groupby(
                        ["생산연도", "상품코드", "상품명"], dropna=False
                    )
                    .agg(
                        현재재고=("현재재고수량", "sum"),
                        재고원가=("현재재고원가", "sum"),
                        컬러수=("칼라", "nunique"),
                        사이즈수=("사이즈", "nunique"),
                    )
                    .reset_index()
                    .sort_values(["생산연도", "상품코드"])
                )
                total_group_stock = code_year_view["현재재고"].sum()
                code_year_view["재고비중"] = (
                    code_year_view["현재재고"] / total_group_stock
                    if total_group_stock
                    else 0
                )
                st.dataframe(
                    code_year_view.style.format(
                        {
                            "생산연도": "{:.0f}",
                            "현재재고": "{:,.0f}",
                            "재고원가": "₩{:,.0f}",
                            "재고비중": "{:.1%}",
                        },
                        na_rep="-",
                    ),
                    width="stretch",
                    hide_index=True,
                )

            with color_tab:
                color_size_pivot = selected_inventory.pivot_table(
                    index="칼라",
                    columns="사이즈",
                    values="현재재고수량",
                    aggfunc="sum",
                    fill_value=0,
                )
                st.markdown("#### 컬러 × 사이즈 현재재고")
                st.dataframe(
                    color_size_pivot.style.format("{:,.0f}"),
                    width="stretch",
                )

                age_summary = (
                    selected_inventory.groupby(["칼라", "사이즈"], dropna=False)
                    .agg(
                        생산연도수=("생산연도", "nunique"),
                        재고품번수=("상품코드", "nunique"),
                    )
                    .reset_index()
                )
                color_view = selected_decision_detail.merge(
                    age_summary, on=["칼라", "사이즈"], how="left", suffixes=("", "_상세")
                )
                color_columns = [
                    "칼라",
                    "사이즈",
                    "현재재고",
                    "생산연도수",
                    "재고품번수",
                    "분석기간판매",
                    "월평균판매",
                    "재고소진개월",
                    "연결품번별 재고",
                ]
                with st.expander("컬러·사이즈별 상세 근거"):
                    st.dataframe(
                        color_view[color_columns].style.format(
                            {
                                "현재재고": "{:,.0f}",
                                "분석기간판매": "{:,.0f}",
                                "월평균판매": "{:,.1f}",
                                "재고소진개월": "{:,.1f}",
                            },
                            na_rep="-",
                        ),
                        width="stretch",
                        hide_index=True,
                    )

            with order_tab:
                if sales is None:
                    st.info("매장 출고·반품 파일을 업로드하면 판매 추세와 발주 제안을 계산합니다.")
                order_code = (
                    selected_decision["제안발주품번"].dropna().iloc[0]
                    if not selected_decision.empty
                    and not selected_decision["제안발주품번"].dropna().empty
                    else f"A{purchase_year % 10}{selected_inventory['대분류'].iloc[0]}"
                    f"{selected_inventory['생산구분코드'].iloc[0]}{selected_name}"
                )
                period_group_sales = pd.DataFrame()
                current_code_sales = pd.DataFrame()
                if sales is not None:
                    period_group_sales = sales.loc[
                        sales["통합상품키"].eq(selected_group_key)
                        & sales["연도"].eq(purchase_year)
                        & sales["월"].between(start_month, end_month)
                    ].copy()
                    current_code_sales = period_group_sales.loc[
                        period_group_sales["상품코드"].eq(order_code)
                    ].copy()

                st.markdown(f"#### {order_code} 발주 판단")
                basis1, basis2, basis3, basis4 = st.columns(4)
                basis1.metric("발주 대상 품번", order_code)
                basis2.metric(
                    "상품군 기간 판매",
                    _format_number(float(period_group_sales["순매출수량"].sum()))
                    if not period_group_sales.empty
                    else "0",
                )
                basis3.metric(
                    "현재 품번 기간 판매",
                    _format_number(float(current_code_sales["순매출수량"].sum()))
                    if not current_code_sales.empty
                    else "0",
                )
                basis4.metric(
                    "분석 기간",
                    f"{purchase_year}년 {start_month}~{end_month}월",
                )

                total_current_stock = float(selected_decision["현재재고"].sum())
                total_monthly_sales = float(selected_decision["월평균판매"].sum())
                total_target_stock = float(selected_decision["목표재고"].sum())
                total_suggested_order = float(selected_decision["제안발주수량"].sum())
                calc1, calc2, calc3, calc4 = st.columns(4)
                calc1.metric("생산연도 통합 현재재고", _format_number(total_current_stock))
                calc2.metric("월평균 판매", f"{total_monthly_sales:,.1f}")
                calc3.metric("목표재고", _format_number(total_target_stock))
                calc4.metric("제안 발주 합계", _format_number(total_suggested_order))

                st.info(
                    "계산식: 월평균판매 = 선택기간 상품군 순매출수량 ÷ 분석개월수 / "
                    "목표재고 = 월평균판매 × 목표 보유개월 / "
                    "제안발주수량 = MAX(목표재고 − 생산연도 통합 현재재고, 0)"
                )
                connected_stock_pivot = selected_inventory.pivot_table(
                    index=["칼라", "사이즈"],
                    columns="상품코드",
                    values="현재재고수량",
                    aggfunc="sum",
                    fill_value=0,
                    margins=True,
                    margins_name="합계",
                )
                st.markdown("#### 연결품번별 현재재고")
                st.caption(
                    "같은 핵심품번으로 묶인 실제 품번의 재고를 컬러·사이즈별로 "
                    "나눠 표시합니다."
                )
                st.dataframe(
                    connected_stock_pivot.style.format("{:,.0f}"),
                    width="stretch",
                )
                order_columns = [
                    "대표상품명",
                    "칼라",
                    "사이즈",
                    "현재재고",
                    "분석기간판매",
                    "월평균판매",
                    "재고소진개월",
                    "목표재고",
                    "제안발주수량",
                    "구매판단",
                    "연결품번별 재고",
                ]
                st.dataframe(
                    selected_decision_detail[order_columns].style.format(
                        {
                            "현재재고": "{:,.0f}",
                            "분석기간판매": "{:,.0f}",
                            "월평균판매": "{:,.1f}",
                            "재고소진개월": "{:,.1f}",
                            "목표재고": "{:,.0f}",
                            "제안발주수량": "{:,.0f}",
                        },
                        na_rep="-",
                    ),
                    width="stretch",
                    hide_index=True,
                )
                st.markdown(
                    f"#### {order_code} 거래처(매장)별 판매 · "
                    f"{purchase_year}년 {start_month}~{end_month}월"
                )
                if current_code_sales.empty:
                    st.info("선택한 기간에 현재 발주 대상 품번의 판매가 없습니다.")
                else:
                    customer_sales = (
                        current_code_sales.groupby("매장명", dropna=False)
                        .agg(
                            판매수량=("순매출수량", "sum"),
                            판매금액=("순매출금액", "sum"),
                            거래건수=("전표번호", "nunique"),
                        )
                        .reset_index()
                        .rename(columns={"매장명": "거래처(매장)"})
                        .sort_values("판매수량", ascending=False)
                    )
                    st.dataframe(
                        customer_sales.style.format(
                            {"판매수량": "{:,.0f}", "판매금액": "₩{:,.0f}"}
                        ),
                        width="stretch",
                        hide_index=True,
                    )
                    monthly_customer_sales = (
                        current_code_sales.pivot_table(
                            index="매장명",
                            columns="월",
                            values="순매출수량",
                            aggfunc="sum",
                            fill_value=0,
                        )
                        .reindex(columns=range(start_month, end_month + 1), fill_value=0)
                        .rename_axis(index="거래처(매장)")
                    )
                    monthly_customer_sales.columns = [
                        f"{int(month)}월" for month in monthly_customer_sales.columns
                    ]
                    monthly_customer_sales["기간 합계"] = monthly_customer_sales.sum(axis=1)
                    monthly_customer_sales = monthly_customer_sales.sort_values(
                        "기간 합계", ascending=False
                    )
                    with st.expander("거래처별 월간 판매수량 보기"):
                        st.dataframe(
                            monthly_customer_sales.style.format("{:,.0f}"),
                            width="stretch",
                        )
                st.caption(
                    "같은 상품군의 모든 생산연도 재고를 컬러·사이즈별로 합산한 뒤 "
                    f"{purchase_year}년 품번으로만 발주수량을 제안합니다."
                )

with mapping_tab:
    st.subheader("아레나 상품코드 자동 분류")
    st.markdown(
        "`브랜드 1자리 + 생산연도 1자리 + 대분류 2자리 + 생산구분 1자리 + 핵심코드 4자리` "
        "규칙을 적용합니다. 예: `A1AC1AC01`, `A3AC1AC01`, `A6AC1AC01` → `A|AC|1|AC01`."
    )
    if inventory is None:
        st.info("ERP 현재 재고 파일을 업로드하면 분류 결과를 확인할 수 있습니다.")
    else:
        classified = inventory.loc[inventory["아레나분류가능"]].copy()
        unclassified = inventory.loc[~inventory["아레나분류가능"]].copy()
        map1, map2, map3 = st.columns(3)
        map1.metric("분류된 재고행", f"{len(classified):,}")
        map2.metric("미분류 재고행", f"{len(unclassified):,}")
        map3.metric(
            "자동 분류율",
            f"{(len(classified) / len(inventory) * 100 if len(inventory) else 0):.1f}%",
        )
        classified_view = (
            classified.groupby(
                [
                    "상품코드",
                    "생산연도",
                    "대분류",
                    "생산구분",
                    "핵심코드",
                    "통합상품키",
                ],
                dropna=False,
            )["현재재고수량"]
            .sum()
            .rename("현재재고")
            .reset_index()
        )
        st.dataframe(
            classified_view.style.format(
                {"생산연도": "{:.0f}", "현재재고": "{:,.0f}"}, na_rep="-"
            ),
            width="stretch",
            hide_index=True,
        )
        if not unclassified.empty:
            with st.expander("미분류 상품코드 보기"):
                st.dataframe(
                    unclassified[["상품코드", "상품명", "현재재고수량"]],
                    width="stretch",
                    hide_index=True,
                )
