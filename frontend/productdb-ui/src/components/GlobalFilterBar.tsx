import { Autocomplete, Paper, Stack, TextField } from "@mui/material";
import { MerchantOverviewItem } from "../api/client";

export interface GlobalFilters {
  timeRange: string;
  currency: string;
  merchantId: number | null;
  search: string;
}

interface GlobalFilterBarProps {
  filters: GlobalFilters;
  onChange: (updates: Partial<GlobalFilters>) => void;
  merchants: MerchantOverviewItem[];
  merchantsLoading?: boolean;
}

const TIME_OPTIONS = [
  { value: "this_month", label: "This Month" },
  { value: "this_year", label: "This Year" },
  { value: "last_12_months", label: "Last 12 Months" },
  { value: "custom", label: "Custom Range" }
];

const CURRENCY_OPTIONS = ["EUR", "USD", "GBP", "CHF"];

const GlobalFilterBar = ({ filters, onChange, merchants, merchantsLoading }: GlobalFilterBarProps) => {
  const merchantValue = merchants.find((m) => m.merchant_id === filters.merchantId) ?? null;

  return (
    <Paper
      elevation={0}
      sx={{
        p: 2.5,
        mb: 2,
        border: "1px solid #E3D4C1",
        background: "#eef3cb"
      }}
    >
      <Stack direction={{ xs: "column", lg: "row" }} spacing={2}>
        <TextField
          select
          label="Time"
          value={filters.timeRange}
          onChange={(event) => onChange({ timeRange: event.target.value })}
          size="small"
          sx={{ minWidth: 200 }}
          SelectProps={{ native: true }}
        >
          {TIME_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </TextField>

        <TextField
          select
          label="Currency"
          value={filters.currency}
          onChange={(event) => onChange({ currency: event.target.value })}
          size="small"
          sx={{ minWidth: 160 }}
          SelectProps={{ native: true }}
        >
          {CURRENCY_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </TextField>

        <Autocomplete
          options={merchants}
          loading={merchantsLoading}
          getOptionLabel={(option) => option.merchant_name}
          value={merchantValue}
          onChange={(_, merchant) => onChange({ merchantId: merchant?.merchant_id ?? null })}
          sx={{ minWidth: 220 }}
          size="small"
          renderInput={(params) => <TextField {...params} label="Merchant" />}
        />

        <TextField
          label="Search"
          placeholder="Search merchants, receipts, products"
          value={filters.search}
          onChange={(event) => onChange({ search: event.target.value })}
          size="small"
          fullWidth
        />
      </Stack>
    </Paper>
  );
};

export default GlobalFilterBar;
