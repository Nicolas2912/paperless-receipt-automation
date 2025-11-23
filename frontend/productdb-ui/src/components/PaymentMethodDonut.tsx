import { useMemo } from "react";
import { Box, Chip, CircularProgress, Paper, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import { fetchPaymentMethodSplit } from "../api/client";
import { GlobalFilters } from "./GlobalFilterBar";
import { formatCurrency } from "../utils/format";
import { resolveDateRange } from "../utils/dateRange";

interface PaymentMethodDonutProps {
  filters: GlobalFilters;
  height?: number;
}

type DonutSlice = {
  key: string;
  label: string;
  value: number;
  count: number;
  color: string;
  percent: number;
  dash: number;
  offset: number;
};

const COLOR_MAP: Record<string, string> = {
  CARD: "#BC6C25",
  CASH: "#5F7D2B",
  OTHER: "#8C5E58"
};

const LABEL_MAP: Record<string, string> = {
  CARD: "Card",
  CASH: "Cash",
  OTHER: "Other"
};

const buildSlices = (raw: Array<{ payment_method: string; total_gross_cents: number; receipt_count: number }>): DonutSlice[] => {
  const baseOrder = ["CARD", "CASH", "OTHER"];
  const merged = baseOrder.map((method) => {
    const match = raw.find((item) => (item.payment_method || "").toUpperCase() === method);
    return {
      key: method,
      label: LABEL_MAP[method] ?? method,
      value: match?.total_gross_cents ?? 0,
      count: match?.receipt_count ?? 0,
      color: COLOR_MAP[method] ?? "#BC6C25"
    };
  });

  const totalValue = merged.reduce((sum, slice) => sum + slice.value, 0);
  const circumference = 2 * Math.PI * 46;
  let accumulated = 0;

  return merged.map((slice) => {
    const percent = totalValue > 0 ? slice.value / totalValue : 0;
    const rawDash = percent * circumference;
    const dash = percent > 0 && rawDash < 2.2 ? 2.2 : rawDash;
    const result: DonutSlice = {
      ...slice,
      percent,
      dash,
      offset: -accumulated
    };
    accumulated += rawDash;
    return result;
  });
};

const PaymentMethodDonut = ({ filters, height = 220 }: PaymentMethodDonutProps) => {
  const range = resolveDateRange(filters.timeRange);
  const query = useQuery({
    queryKey: ["payment-method-split", range.from, range.to],
    queryFn: () => fetchPaymentMethodSplit({ dateFrom: range.from, dateTo: range.to })
  });

  const slices = useMemo(() => buildSlices(query.data?.items ?? []), [query.data?.items]);
  const totalGross = useMemo(() => slices.reduce((sum, slice) => sum + slice.value, 0), [slices]);
  const totalReceipts = useMemo(() => slices.reduce((sum, slice) => sum + slice.count, 0), [slices]);
  const hasData = totalGross > 0 || totalReceipts > 0;

  const chartSize = 150;
  const center = chartSize / 2;
  const radius = 48;
  const thickness = 12;
  const circumference = 2 * Math.PI * radius;

  return (
    <Paper
      elevation={0}
      sx={{
        p: 2,
        height,
        width: "100%",
        border: "1px solid #E3D4C1",
        display: "flex",
        flexDirection: "column",
        gap: 1.1
      }}
    >
      <Stack direction="row" justifyContent="flex-start" spacing={2}>
        <Stack spacing={0.25}>
          <Typography variant="subtitle1" fontWeight={800}>
            Payment method split
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip size="small" label={filters.timeRange === "custom" ? "Custom" : "Filtered"} />
            <Typography variant="caption" color="text.secondary">
              {totalReceipts} receipts
            </Typography>
          </Stack>
        </Stack>
      </Stack>

      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "0.95fr 1.05fr" },
          gap: 0.8,
          flex: 1,
          alignItems: "flex-start",
          justifyContent: "center"
        }}
      >
        <Box
          sx={{
            position: "relative",
            minHeight: 150,
            maxWidth: 175,
            width: "100%",
            mx: "auto",
            mt: -2.5,
            aspectRatio: "1 / 1"
          }}
        >
          {query.isLoading ? (
            <Stack height="100%" alignItems="center" justifyContent="center">
              <CircularProgress size={24} />
            </Stack>
          ) : query.isError ? (
            <Stack height="100%" alignItems="center" justifyContent="center" spacing={0.5}>
              <Typography variant="subtitle2" fontWeight={700}>
                Could not load payment split
              </Typography>
              <Typography variant="caption" color="text.secondary" textAlign="center">
                {axios.isAxiosError(query.error) ? query.error.message : "Unexpected error"}
              </Typography>
            </Stack>
          ) : !hasData ? (
            <Stack height="100%" alignItems="center" justifyContent="center" spacing={0.5}>
              {query.data?.items?.length === 0 ? (
                <>
                  <Typography variant="subtitle2" fontWeight={700}>
                    Not available on this API version
                  </Typography>
                  <Typography variant="caption" color="text.secondary" textAlign="center">
                    Update the backend or restart after pulling latest code.
                  </Typography>
                </>
              ) : (
                <>
                  <Typography variant="subtitle2" fontWeight={700}>
                    No payment data
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    Add receipts to see the split.
                  </Typography>
                </>
              )}
            </Stack>
          ) : (
            <Box sx={{ position: "relative", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <svg width="100%" height="100%" viewBox={`0 0 ${chartSize} ${chartSize}`}>
                <circle
                  cx={center}
                  cy={center}
                  r={radius}
                  fill="none"
                  stroke="#F4E7D7"
                  strokeWidth={thickness}
                  transform={`rotate(-90 ${center} ${center})`}
                />
                {slices.map((slice) => (
                  <circle
                    key={slice.key}
                    cx={center}
                    cy={center}
                    r={radius}
                    fill="none"
                    stroke={slice.color}
                    strokeWidth={thickness}
                    strokeDasharray={`${slice.dash} ${circumference}`}
                    strokeDashoffset={slice.offset}
                    strokeLinecap="round"
                    transform={`rotate(-90 ${center} ${center})`}
                  />
                ))}
              </svg>
              <Stack
                spacing={0}
                alignItems="center"
                sx={{
                  position: "absolute",
                  left: "50%",
                  top: "50%",
                  transform: "translate(-50%, -50%)"
                }}
              >
                <Typography variant="caption" color="text.secondary">
                  Gross
                </Typography>
                <Typography variant="subtitle1" fontWeight={800}>
                  {formatCurrency(totalGross, filters.currency)}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {totalReceipts} receipts
                </Typography>
              </Stack>
            </Box>
          )}
        </Box>

        <Stack spacing={0.6} justifyContent="flex-start" pt={0} mt={-1}>
          {slices.map((slice) => (
            <Stack key={slice.key} direction="row" alignItems="center" spacing={1}>
              <Box sx={{ width: 12, height: 12, borderRadius: "50%", bgcolor: slice.color, border: "1px solid #E3D4C1" }} />
              <Stack spacing={0}>
                <Typography variant="body2" fontWeight={700}>
                  {slice.label}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {formatCurrency(slice.value, filters.currency)} • {slice.count} receipts
                </Typography>
              </Stack>
              <Typography variant="body2" fontWeight={700} sx={{ marginLeft: "auto" }}>
                {(slice.percent * 100).toFixed(1)}%
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Box>
    </Paper>
  );
};

export default PaymentMethodDonut;
