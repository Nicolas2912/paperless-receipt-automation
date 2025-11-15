import axios from "axios";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "/api";

type Direction = "asc" | "desc";

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

export const fetchSummary = async (): Promise<SummaryResponse> => {
  const { data } = await api.get<SummaryResponse>("/summary");
  return data;
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
