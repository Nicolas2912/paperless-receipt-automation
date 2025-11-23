import axios from "axios";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";

type Direction = "asc" | "desc";

export interface SummaryRange {
  filters: {
    date_from: string | null;
    date_to: string | null;
  };
  counts: {
    receipts: number;
    receipt_items: number;
    merchants: number;
    addresses: number;
  };
  totals: {
    total_net_cents: number;
    total_tax_cents: number;
    total_gross_cents: number;
  };
  timespan: {
    first_purchase: string | null;
    last_purchase: string | null;
  };
  daily_totals: Array<{
    date: string;
    total_gross_cents: number;
    receipt_count: number;
  }>;
}

export interface SummaryResponse {
  counts: Record<string, number>;
  totals: {
    total_net_cents: number;
    total_tax_cents: number;
    total_gross_cents: number;
  };
  timespan: {
    first_purchase: string | null;
    last_purchase: string | null;
  };
  range: SummaryRange;
}

export interface ReceiptOverviewItem {
  receipt_id: number;
  purchase_date_time: string;
  currency: string;
  payment_method: string;
  total_net: number | null;
  total_tax: number | null;
  total_gross: number | null;
  merchant_id: number;
  merchant_name: string;
  merchant_city: string | null;
  merchant_country: string | null;
  item_count: number;
}

export interface ReceiptsOverviewResponse {
  total: number;
  items: ReceiptOverviewItem[];
  limit: number;
  offset: number;
  page: number;
}

export interface ReceiptItem {
  item_id: number;
  product_name: string;
  quantity: number;
  unit_price_net: number | null;
  unit_price_gross: number | null;
  tax_rate: number;
  line_net: number | null;
  line_tax: number | null;
  line_gross: number | null;
  line_type: string;
  created_at: string;
}

export interface ExtractionRun {
  run_id: number;
  model_name: string;
  started_at: string | null;
  finished_at: string | null;
  status: string | null;
  raw_content_id: number | null;
  notes: string | null;
}

export interface ReceiptDetail extends ReceiptOverviewItem {
  source_file_id: number | null;
  raw_content_id: number | null;
  created_at: string;
  address_id: number | null;
  street: string | null;
  city: string | null;
  postal_code: string | null;
  country: string | null;
  file_id: number | null;
  filename: string | null;
  mime_type: string | null;
  byte_size: number | null;
  sha256: string | null;
  items: ReceiptItem[];
  extraction_runs: ExtractionRun[];
  raw_content: string | null;
}

export interface MerchantOverviewItem {
  merchant_id: number;
  merchant_name: string;
  created_at: string | null;
  address_id: number | null;
  city: string | null;
  country: string | null;
  receipt_count: number;
  total_gross_cents: number;
}

export interface MerchantsResponse {
  items: MerchantOverviewItem[];
}

export interface TableRowsResponse {
  total: number;
  items: Array<Record<string, unknown>>;
  limit: number;
  offset: number;
}

export interface ReceiptsQuery {
  page?: number;
  limit?: number;
  search?: string;
  merchantId?: number;
  sort?: "purchase_date_time" | "total_gross" | "merchant" | "item_count";
  direction?: Direction;
}

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15_000
});

export interface SummaryFilters {
  dateFrom?: string | null;
  dateTo?: string | null;
}

export interface SpendTimeseriesPoint {
  date: string;
  total_gross_cents: number;
  receipt_count: number;
}

export interface SpendTimeseriesResponse {
  filters: {
    date_from: string | null;
    date_to: string | null;
  };
  points: SpendTimeseriesPoint[];
}

export interface MonthlySpendPoint {
  month: string;
  total_gross_cents: number;
  receipt_count: number;
}

export interface MonthlySpendResponse {
  filters: {
    date_from: string | null;
    date_to: string | null;
  };
  points: MonthlySpendPoint[];
}

export interface MerchantSpendItem {
  merchant_id: number;
  merchant_name: string;
  total_gross_cents: number;
  receipt_count: number;
}

export interface MerchantSpendResponse {
  filters: {
    date_from: string | null;
    date_to: string | null;
  };
  items: MerchantSpendItem[];
}

export interface PaymentMethodSplitItem {
  payment_method: string;
  total_gross_cents: number;
  receipt_count: number;
}

export interface PaymentMethodSplitResponse {
  filters: {
    date_from: string | null;
    date_to: string | null;
  };
  items: PaymentMethodSplitItem[];
}

export interface TaxRateSplitItem {
  tax_rate: number;
  line_gross_cents: number;
  item_count: number;
}

export interface TaxRateSplitResponse {
  filters: {
    date_from: string | null;
    date_to: string | null;
  };
  items: TaxRateSplitItem[];
}

export const fetchSummary = async (filters: SummaryFilters = {}): Promise<SummaryResponse> => {
  const params = new URLSearchParams();
  if (filters.dateFrom) {
    params.set("from", filters.dateFrom);
  }
  if (filters.dateTo) {
    params.set("to", filters.dateTo);
  }
  const { data } = await api.get<SummaryResponse>("/summary", { params });
  return data;
};

export const fetchSpendTimeseries = async (
  filters: SummaryFilters = {}
): Promise<SpendTimeseriesResponse> => {
  const params = new URLSearchParams();
  if (filters.dateFrom) {
    params.set("from", filters.dateFrom);
  }
  if (filters.dateTo) {
    params.set("to", filters.dateTo);
  }
  const { data } = await api.get<SpendTimeseriesResponse>("/timeseries/spend", { params });
  return data;
};

export const fetchMonthlySpend = async (
  filters: SummaryFilters = {}
): Promise<MonthlySpendResponse> => {
  const params = new URLSearchParams();
  if (filters.dateFrom) {
    params.set("from", filters.dateFrom);
  }
  if (filters.dateTo) {
    params.set("to", filters.dateTo);
  }
  const { data } = await api.get<MonthlySpendResponse>("/analytics/monthly_spend", { params });
  return data;
};

export const fetchMerchantSpend = async (
  filters: SummaryFilters = {},
  limit = 8
): Promise<MerchantSpendResponse> => {
  const params = new URLSearchParams();
  if (filters.dateFrom) {
    params.set("from", filters.dateFrom);
  }
  if (filters.dateTo) {
    params.set("to", filters.dateTo);
  }
  params.set("limit", String(limit));
  const { data } = await api.get<MerchantSpendResponse>("/analytics/merchant_spend", { params });
  return data;
};

export const fetchPaymentMethodSplit = async (
  filters: SummaryFilters = {}
): Promise<PaymentMethodSplitResponse> => {
  const params = new URLSearchParams();
  if (filters.dateFrom) {
    params.set("from", filters.dateFrom);
  }
  if (filters.dateTo) {
    params.set("to", filters.dateTo);
  }
  try {
    const { data } = await api.get<PaymentMethodSplitResponse>("/analytics/payment_method_split", { params });
    return data;
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404) {
      return { filters: { date_from: null, date_to: null }, items: [] };
    }
    throw error;
  }
};

export const fetchTaxRateSplit = async (filters: SummaryFilters = {}): Promise<TaxRateSplitResponse> => {
  const params = new URLSearchParams();
  if (filters.dateFrom) {
    params.set("from", filters.dateFrom);
  }
  if (filters.dateTo) {
    params.set("to", filters.dateTo);
  }
  try {
    const { data } = await api.get<TaxRateSplitResponse>("/analytics/tax_rate_split", { params });
    return data;
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404) {
      return { filters: { date_from: null, date_to: null }, items: [] };
    }
    throw error;
  }
};

export const fetchReceipts = async (query: ReceiptsQuery = {}): Promise<ReceiptsOverviewResponse> => {
  const params = new URLSearchParams();
  params.set("page", String(query.page ?? 0));
  params.set("limit", String(query.limit ?? 25));
  if (query.search) {
    params.set("search", query.search);
  }
  if (query.merchantId !== undefined) {
    params.set("merchant_id", String(query.merchantId));
  }
  if (query.sort) {
    params.set("sort", query.sort);
  }
  if (query.direction) {
    params.set("direction", query.direction);
  }
  const { data } = await api.get<ReceiptsOverviewResponse>("/receipts", { params });
  return data;
};

export const fetchReceiptDetail = async (receiptId: number): Promise<ReceiptDetail> => {
  const { data } = await api.get<ReceiptDetail>(`/receipts/${receiptId}`);
  return data;
};

export const fetchMerchants = async (): Promise<MerchantsResponse> => {
  const { data } = await api.get<MerchantsResponse>("/merchants");
  return data;
};

export const fetchTableRows = async (
  table: string,
  limit = 100,
  offset = 0
): Promise<TableRowsResponse> => {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  const { data } = await api.get<TableRowsResponse>(`/tables/${table}`, { params });
  return data;
};

export { API_BASE_URL };
