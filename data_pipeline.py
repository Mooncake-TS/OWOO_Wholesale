"""ERP 엑셀 업로드, 컬럼 검증, 상세행 정제를 담당하는 모듈."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
import re
from typing import BinaryIO, Literal

import numpy as np
import pandas as pd


SourceKind = Literal["sales", "purchases", "inventory"]
ExcelSource = str | Path | BytesIO | BinaryIO


@dataclass(frozen=True)
class SourceSpec:
    label: str
    required_columns: tuple[str, ...]
    signature_columns: tuple[str, ...]


@dataclass(frozen=True)
class LoadMeta:
    label: str
    header_row: int
    raw_rows: int
    clean_rows: int
    dropped_rows: int
    date_min: date | None = None
    date_max: date | None = None
    snapshot_date: date | None = None


class SchemaError(ValueError):
    """업로드된 파일에서 필요한 ERP 컬럼을 찾지 못했을 때 발생한다."""


SPECS: dict[SourceKind, SourceSpec] = {
    "sales": SourceSpec(
        label="매장 출고·반품",
        required_columns=(
            "구분",
            "일자",
            "창고",
            "상품코드",
            "칼라",
            "사이즈",
            "수량",
            "출고가금액",
        ),
        signature_columns=("매장명", "매장코드", "전표번호", "상품명", "현재가"),
    ),
    "purchases": SourceSpec(
        label="상품 입고·반품",
        required_columns=(
            "조회구분",
            "조회일자",
            "상품코드",
            "칼라",
            "사이즈",
            "총수량",
            "입고금액",
        ),
        signature_columns=("창고명", "거래처명", "전표번호", "상품명", "입고단가"),
    ),
    "inventory": SourceSpec(
        label="현재 재고",
        required_columns=("창고명", "상품코드", "상품명", "칼라", "사이즈", "수량", "원가 합계"),
        signature_columns=("창고코드", "품목", "거래처명", "자사바코드", "판매가 합계"),
    ),
}


ARENA_CODE_PATTERN = r"^A(?P<생산연도코드>[0-9A-Z])(?P<대분류>[A-Z]{2})(?P<생산구분코드>[12])(?P<핵심코드>[A-Z0-9]{4})$"


def _rewind(source: ExcelSource) -> None:
    if hasattr(source, "seek"):
        source.seek(0)


def _normalize_header(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _preview_excel(source: ExcelSource, rows: int = 20) -> pd.DataFrame:
    _rewind(source)
    preview = pd.read_excel(source, sheet_name=0, header=None, nrows=rows, engine="openpyxl")
    _rewind(source)
    return preview


def _find_header_row(preview: pd.DataFrame, spec: SourceSpec) -> int:
    best_row = -1
    best_score = -1
    required = set(spec.required_columns)
    signature = set(spec.signature_columns)

    for row_number, row in preview.iterrows():
        values = {_normalize_header(value) for value in row.tolist()}
        score = len(values & required) * 10 + len(values & signature)
        if score > best_score:
            best_row = int(row_number)
            best_score = score

    minimum_score = max(30, len(required) * 10 - 20)
    if best_score < minimum_score:
        found = sorted(
            {
                _normalize_header(value)
                for value in preview.to_numpy().ravel().tolist()
                if _normalize_header(value)
            }
        )
        raise SchemaError(
            f"{spec.label} 헤더를 찾지 못했습니다. "
            f"필수 컬럼: {', '.join(spec.required_columns)} / "
            f"확인된 값 일부: {', '.join(found[:12])}"
        )
    return best_row


def _read_raw(source: ExcelSource, kind: SourceKind) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    spec = SPECS[kind]
    preview = _preview_excel(source)
    header_row = _find_header_row(preview, spec)
    _rewind(source)
    frame = pd.read_excel(source, sheet_name=0, header=header_row, engine="openpyxl")
    _rewind(source)

    frame.columns = [_normalize_header(column) for column in frame.columns]
    frame = frame.dropna(axis=0, how="all").dropna(axis=1, how="all")

    missing = [column for column in spec.required_columns if column not in frame.columns]
    if missing:
        raise SchemaError(f"{spec.label} 파일에 필요한 컬럼이 없습니다: {', '.join(missing)}")
    return frame, preview, header_row


def _to_number(series: pd.Series) -> pd.Series:
    cleaned = series.astype("string").str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


def _to_datetime(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    numeric = pd.to_numeric(series, errors="coerce")
    numeric_mask = parsed.isna() & numeric.notna() & numeric.between(20_000, 80_000)
    if numeric_mask.any():
        parsed.loc[numeric_mask] = pd.to_datetime(
            numeric.loc[numeric_mask], unit="D", origin="1899-12-30", errors="coerce"
        )
    return parsed


def _key_part(series: pd.Series) -> pd.Series:
    result = series.astype("string").fillna("").str.strip()
    return result.str.replace(r"\.0$", "", regex=True)


def _wholesale_warehouse_mask(frame: pd.DataFrame, column: str) -> pd.Series:
    """창고명이 정확히 '총판'인 상세행만 선택한다."""
    warehouse = frame[column].astype("string").fillna("").str.strip()
    return warehouse.eq("총판")


def add_product_classification(frame: pd.DataFrame) -> pd.DataFrame:
    """아레나 9자리 품번을 생산연도를 제외한 상품군으로 분류한다."""
    result = frame.copy()
    codes = _key_part(result["상품코드"]).str.upper()
    parsed = codes.str.extract(ARENA_CODE_PATTERN)
    classified = parsed["핵심코드"].notna()

    result["아레나분류가능"] = classified
    result["생산연도코드"] = parsed["생산연도코드"].fillna("")
    production_year = (
        pd.to_numeric(parsed["생산연도코드"], errors="coerce") + 2020
    ).astype("Int64")
    result["생산연도정렬"] = production_year.fillna(-1).astype("Int64")
    result["생산연도"] = production_year.astype("string")
    result.loc[classified & production_year.isna(), "생산연도"] = "기타연도"
    result.loc[~classified, "생산연도"] = ""
    result["대분류"] = parsed["대분류"].fillna("")
    result["생산구분코드"] = parsed["생산구분코드"].fillna("")
    result["생산구분"] = result["생산구분코드"].map(
        {"1": "국내생산", "2": "일본생산"}
    ).fillna("미분류")
    result["핵심코드"] = parsed["핵심코드"].fillna("")

    result["통합상품키"] = "미분류|" + codes
    result.loc[classified, "통합상품키"] = (
        "A|"
        + result.loc[classified, "대분류"]
        + "|"
        + result.loc[classified, "생산구분코드"]
        + "|"
        + result.loc[classified, "핵심코드"]
    )
    result["상품군표시"] = "미분류"
    result.loc[classified, "상품군표시"] = result.loc[classified, "핵심코드"]
    return result


def _add_item_keys(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    for column in ("상품코드", "칼라", "사이즈"):
        result[column] = _key_part(result[column])
    result = add_product_classification(result)
    result["원본SKU키"] = (
        result["상품코드"] + "|" + result["칼라"] + "|" + result["사이즈"]
    )
    result["통합SKU키"] = (
        result["통합상품키"] + "|" + result["칼라"] + "|" + result["사이즈"]
    )
    result["분석품번"] = result["통합상품키"]
    return result


def _date_bounds(series: pd.Series) -> tuple[date | None, date | None]:
    valid = series.dropna()
    if valid.empty:
        return None, None
    return valid.min().date(), valid.max().date()


def _extract_snapshot_date(preview: pd.DataFrame) -> date | None:
    text = " ".join(
        str(value) for value in preview.to_numpy().ravel().tolist() if not pd.isna(value)
    )
    match = re.search(r"재고일자\s*:\s*(\d{8})", text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def load_sales(source: ExcelSource) -> tuple[pd.DataFrame, LoadMeta]:
    frame, _preview, header_row = _read_raw(source, "sales")
    raw_rows = len(frame)
    frame["분석일자"] = _to_datetime(frame["일자"])
    transaction_type = frame["구분"].astype("string").fillna("").str.strip()
    product_code = _key_part(frame["상품코드"])
    valid = (
        frame["분석일자"].notna()
        & transaction_type.str.contains("출고|반품", regex=True)
        & _wholesale_warehouse_mask(frame, "창고")
        & product_code.ne("")
        & ~product_code.str.contains("소계|합계", regex=True)
    )
    frame = frame.loc[valid].copy()
    frame = _add_item_keys(frame)

    quantity = _to_number(frame["수량"]).fillna(0)
    amount = _to_number(frame["출고가금액"]).fillna(0)
    is_return = transaction_type.loc[frame.index].str.contains("반품")
    frame["순매출수량"] = np.where(is_return, -quantity.abs(), quantity.abs())
    frame["순매출금액"] = np.where(is_return, -amount.abs(), amount.abs())
    frame["연도"] = frame["분석일자"].dt.year.astype("Int64")
    frame["월"] = frame["분석일자"].dt.month.astype("Int64")
    frame["연월"] = frame["분석일자"].dt.to_period("M").astype(str)

    date_min, date_max = _date_bounds(frame["분석일자"])
    meta = LoadMeta(
        label=SPECS["sales"].label,
        header_row=header_row + 1,
        raw_rows=raw_rows,
        clean_rows=len(frame),
        dropped_rows=raw_rows - len(frame),
        date_min=date_min,
        date_max=date_max,
    )
    return frame.reset_index(drop=True), meta


def load_purchases(source: ExcelSource) -> tuple[pd.DataFrame, LoadMeta]:
    frame, _preview, header_row = _read_raw(source, "purchases")
    raw_rows = len(frame)
    frame["분석일자"] = _to_datetime(frame["조회일자"])
    transaction_type = frame["조회구분"].astype("string").fillna("").str.strip()
    product_code = _key_part(frame["상품코드"])
    valid = (
        frame["분석일자"].notna()
        & transaction_type.str.contains("입고|반품", regex=True)
        & _wholesale_warehouse_mask(frame, "창고명")
        & product_code.ne("")
        & ~product_code.str.contains("소계|합계", regex=True)
    )
    frame = frame.loc[valid].copy()
    frame = _add_item_keys(frame)

    quantity = _to_number(frame["총수량"]).fillna(0)
    amount = _to_number(frame["입고금액"]).fillna(0)
    is_return = transaction_type.loc[frame.index].str.contains("반품")
    frame["순매입수량"] = np.where(is_return, -quantity.abs(), quantity.abs())
    frame["순매입금액"] = np.where(is_return, -amount.abs(), amount.abs())
    frame["연도"] = frame["분석일자"].dt.year.astype("Int64")
    frame["월"] = frame["분석일자"].dt.month.astype("Int64")
    frame["연월"] = frame["분석일자"].dt.to_period("M").astype(str)

    date_min, date_max = _date_bounds(frame["분석일자"])
    meta = LoadMeta(
        label=SPECS["purchases"].label,
        header_row=header_row + 1,
        raw_rows=raw_rows,
        clean_rows=len(frame),
        dropped_rows=raw_rows - len(frame),
        date_min=date_min,
        date_max=date_max,
    )
    return frame.reset_index(drop=True), meta


def load_inventory(source: ExcelSource) -> tuple[pd.DataFrame, LoadMeta]:
    frame, preview, header_row = _read_raw(source, "inventory")
    raw_rows = len(frame)
    product_code = _key_part(frame["상품코드"])
    quantity = _to_number(frame["수량"])
    valid = (
        product_code.ne("")
        & quantity.notna()
        & _wholesale_warehouse_mask(frame, "창고명")
        & ~product_code.str.contains("소계|합계", regex=True)
    )
    if "No" in frame.columns:
        valid &= _to_number(frame["No"]).notna()

    frame = frame.loc[valid].copy()
    frame = _add_item_keys(frame)
    frame["현재재고수량"] = _to_number(frame["수량"]).fillna(0)
    frame["현재재고원가"] = _to_number(frame["원가 합계"]).fillna(0)
    if "판매가 합계" in frame.columns:
        frame["현재재고판매가"] = _to_number(frame["판매가 합계"]).fillna(0)
    else:
        frame["현재재고판매가"] = 0.0

    meta = LoadMeta(
        label=SPECS["inventory"].label,
        header_row=header_row + 1,
        raw_rows=raw_rows,
        clean_rows=len(frame),
        dropped_rows=raw_rows - len(frame),
        snapshot_date=_extract_snapshot_date(preview),
    )
    return frame.reset_index(drop=True), meta


def same_period_frames(
    frame: pd.DataFrame,
    analysis_year: int,
    start_month: int = 1,
    end_month: int = 12,
    date_column: str = "분석일자",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp | None]:
    """선택 연도와 전년도의 동일한 시작월~종료월 자료를 반환한다."""
    month_mask = frame[date_column].dt.month.between(start_month, end_month)
    current = frame.loc[
        frame[date_column].dt.year.eq(analysis_year) & month_mask
    ].copy()
    previous = frame.loc[
        frame[date_column].dt.year.eq(analysis_year - 1)
        & month_mask
    ].copy()
    current_cutoff = current[date_column].max() if not current.empty else None
    return current, previous, current_cutoff


def monthly_comparison(
    frame: pd.DataFrame,
    analysis_year: int,
    quantity_column: str,
    amount_column: str,
    start_month: int = 1,
    end_month: int = 12,
) -> pd.DataFrame:
    current, previous, _cutoff = same_period_frames(
        frame, analysis_year, start_month=start_month, end_month=end_month
    )
    rows: list[dict[str, float | int]] = []
    for month in range(start_month, end_month + 1):
        current_month = current.loc[current["월"].eq(month)]
        previous_month = previous.loc[previous["월"].eq(month)]
        rows.append(
            {
                "월": month,
                f"{analysis_year} 수량": float(current_month[quantity_column].sum()),
                f"{analysis_year - 1} 수량": float(previous_month[quantity_column].sum()),
                f"{analysis_year} 금액": float(current_month[amount_column].sum()),
                f"{analysis_year - 1} 금액": float(previous_month[amount_column].sum()),
            }
        )
    return pd.DataFrame(rows)


def build_inventory_group_summary(inventory: pd.DataFrame) -> pd.DataFrame:
    """아레나 재고를 생산연도를 제거한 상품군 단위로 요약한다."""
    classified = inventory.loc[inventory["아레나분류가능"]].copy()
    if classified.empty:
        return pd.DataFrame()
    return (
        classified.groupby(
            ["통합상품키", "상품군표시", "대분류", "생산구분코드", "생산구분"],
            dropna=False,
        )
        .agg(
            대표상품명=("상품명", "first"),
            현재재고=("현재재고수량", "sum"),
            재고원가=("현재재고원가", "sum"),
            재고판매가=("현재재고판매가", "sum"),
            생산연도수=("생산연도", "nunique"),
            실제품번수=("상품코드", "nunique"),
            컬러사이즈수=("통합SKU키", "nunique"),
        )
        .reset_index()
        .sort_values(["현재재고", "상품군표시"], ascending=[False, True])
        .reset_index(drop=True)
    )


def build_purchase_decision(
    inventory: pd.DataFrame,
    sales: pd.DataFrame,
    purchases: pd.DataFrame | None = None,
    target_months: float = 3.0,
    recent_days: int = 90,
    analysis_year: int | None = None,
    start_month: int = 1,
    end_month: int = 12,
) -> pd.DataFrame:
    """상품군+컬러+사이즈별 통합재고와 통합판매를 결합한다."""
    dimensions = [
        "통합SKU키",
        "통합상품키",
        "상품군표시",
        "대분류",
        "생산구분코드",
        "생산구분",
        "칼라",
        "사이즈",
    ]
    classified_inventory = inventory.loc[inventory["아레나분류가능"]].copy()
    inventory_summary = (
        classified_inventory.groupby(dimensions, dropna=False)
        .agg(
            현재재고=("현재재고수량", "sum"),
            재고원가=("현재재고원가", "sum"),
            재고판매가=("현재재고판매가", "sum"),
            재고품번수=("상품코드", "nunique"),
        )
        .reset_index()
    )

    classified_sales = sales.loc[sales["아레나분류가능"]].copy() if not sales.empty else sales.copy()
    if classified_sales.empty:
        sales_summary = pd.DataFrame(columns=dimensions + ["분석기간판매"])
    else:
        if analysis_year is None:
            latest_date = classified_sales["분석일자"].max()
            recent_start = latest_date - pd.Timedelta(days=recent_days - 1)
            analysis_sales = classified_sales.loc[
                classified_sales["분석일자"].between(recent_start, latest_date)
            ].copy()
        else:
            analysis_sales = classified_sales.loc[
                classified_sales["연도"].eq(analysis_year)
                & classified_sales["월"].between(start_month, end_month)
            ].copy()
        sales_summary = (
            analysis_sales.groupby(dimensions, dropna=False)["순매출수량"]
            .sum()
            .clip(lower=0)
            .rename("분석기간판매")
            .reset_index()
        )

    result = inventory_summary.merge(sales_summary, on=dimensions, how="outer")
    for column in ("현재재고", "재고원가", "재고판매가", "재고품번수", "분석기간판매"):
        result[column] = result[column].fillna(0)

    reference_frames = [classified_inventory]
    if not classified_sales.empty:
        reference_frames.append(classified_sales)
    if purchases is not None and not purchases.empty:
        reference_frames.append(purchases.loc[purchases["아레나분류가능"]].copy())
    references = pd.concat(reference_frames, ignore_index=True, sort=False)
    # Streamlit에 이전 스키마로 캐시된 데이터가 남아 있어도 정렬키를 복원한다.
    if "생산연도정렬" in references.columns:
        year_sort = pd.to_numeric(references["생산연도정렬"], errors="coerce")
    else:
        year_sort = pd.Series(pd.NA, index=references.index, dtype="Float64")
    year_sort = year_sort.fillna(
        pd.to_numeric(references["생산연도"], errors="coerce")
    )
    references["생산연도정렬"] = year_sort.fillna(-1).astype("Int64")
    references = references.sort_values(
        ["통합SKU키", "생산연도정렬", "상품코드"], na_position="first"
    )
    latest_reference = (
        references.groupby("통합SKU키", dropna=False).tail(1)[
            ["통합SKU키", "상품코드", "상품명", "생산연도"]
        ]
        .rename(
            columns={
                "상품코드": "제안발주품번",
                "상품명": "대표상품명",
                "생산연도": "최신생산연도",
            }
        )
    )
    connected_codes = (
        references.groupby("통합SKU키", dropna=False)["상품코드"]
        .agg(lambda values: ", ".join(sorted(set(str(value) for value in values if pd.notna(value)))))
        .rename("연결품번")
        .reset_index()
    )
    result = result.merge(latest_reference, on="통합SKU키", how="left")
    result = result.merge(connected_codes, on="통합SKU키", how="left")

    if analysis_year is not None:
        result["제안발주품번"] = (
            "A"
            + str(analysis_year % 10)
            + result["대분류"]
            + result["생산구분코드"]
            + result["상품군표시"]
        )
        result["최신생산연도"] = analysis_year

    period_months = (
        end_month - start_month + 1 if analysis_year is not None else recent_days / 30.0
    )
    result["월평균판매"] = (
        pd.to_numeric(result["분석기간판매"], errors="coerce").fillna(0).astype(float)
        / period_months
    )
    monthly_sales = result["월평균판매"].replace(0, np.nan)
    current_stock = pd.to_numeric(result["현재재고"], errors="coerce").fillna(0).astype(float)
    result["재고소진개월"] = current_stock.div(monthly_sales)
    result["목표재고"] = np.ceil(result["월평균판매"] * target_months)
    calculated_order = np.ceil(
        (result["목표재고"] - result["현재재고"]).clip(lower=0)
    )
    result["제안발주수량"] = np.where(
        result["월평균판매"].gt(0), calculated_order, 0
    )

    conditions = [
        result["월평균판매"].le(0),
        result["현재재고"].lt(result["월평균판매"]),
        result["현재재고"].gt(result["월평균판매"] * 6),
    ]
    result["구매판단"] = np.select(
        conditions,
        ["판매 없음", "부족 위험", "과재고"],
        default="적정",
    )
    return result.sort_values(
        ["제안발주수량", "분석기간판매"], ascending=[False, False]
    ).reset_index(drop=True)
