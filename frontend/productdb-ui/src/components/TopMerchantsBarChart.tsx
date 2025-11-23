import { useMemo } from "react";
import { Box, Chip, CircularProgress, Paper, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import { fetchMerchantSpend } from "../api/client";
import { formatCurrency } from "../utils/format";
import { resolveDateRange } from "../utils/dateRange";
import { GlobalFilters } from "./GlobalFilterBar";

interface TopMerchantsBarChartProps {
  filters: GlobalFilters;
  height?: number;
  limit?: number;
}

const TopMerchantsBarChart = ({ filters, height = 220, limit = 10 }: TopMerchantsBarChartProps) => {
  const range = resolveDateRange(filters.timeRange);
  const query = useQuery({
    queryKey: ["merchant-spend", range.from, range.to, limit],
    queryFn: () => fetchMerchantSpend({ dateFrom: range.from, dateTo: range.to }, limit)
  });

  const stats = useMemo(() => {
    const items = query.data?.items ?? [];
    const maxGross = Math.max(...items.map((item) => item.total_gross_cents ?? 0), 0);
    const totalGross = items.reduce((sum, item) => sum + (item.total_gross_cents ?? 0), 0);
    const totalReceipts = items.reduce((sum, item) => sum + (item.receipt_count ?? 0), 0);
    return { maxGross, totalGross, totalReceipts };
  }, [query.data?.items]);

  return (
    <Paper
      elevation={0}
      sx={{
        height,
        p: 2,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        border: "1px solid #E3D4C1",
        background: "linear-gradient(180deg, #FFF8EE 0%, #F6E6D4 100%)"
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1.5}>
        <Stack spacing={0.25}>
          <Typography variant="subtitle1" fontWeight={800}>
            Top merchants by spend
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip size="small" label={filters.timeRange === "custom" ? "Custom" : "Filtered"} />
            <Typography variant="caption" color="text.secondary">
              {query.data?.items?.length ?? 0} merchants • {stats.totalReceipts} receipts
            </Typography>
          </Stack>
        </Stack>
        <Typography variant="caption" color="text.secondary">
          {formatCurrency(stats.totalGross, filters.currency)} total
        </Typography>
      </Stack>

      <Box sx={{ flex: 1, display: "flex", flexDirection: "column", gap: 1, overflow: "hidden" }}>
        {query.isLoading ? (
          <Stack height="100%" alignItems="center" justifyContent="center">
            <CircularProgress size={22} />
          </Stack>
        ) : query.isError ? (
          <Stack spacing={0.5}>
            <Typography variant="subtitle2" fontWeight={700}>
              Could not load merchants
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {axios.isAxiosError(query.error) ? query.error.message : "Unexpected error"}
            </Typography>
          </Stack>
        ) : (query.data?.items?.length ?? 0) === 0 ? (
          <Stack spacing={0.5} justifyContent="center" alignItems="flex-start" sx={{ flex: 1 }}>
            <Typography variant="subtitle2" fontWeight={700}>
              No merchant spend yet
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Add receipts to see the ranking.
            </Typography>
          </Stack>
        ) : (
          <Stack spacing={1} sx={{ overflowY: "auto", pr: 0.5 }}>
            {query.data?.items?.map((item, index) => {
              const value = item.total_gross_cents ?? 0;
              const ratio = stats.maxGross > 0 ? value / stats.maxGross : 0;
              const widthPercent = Math.max(ratio * 100, value > 0 ? 6 : 0);
              return (
                <Stack key={item.merchant_id} direction="row" alignItems="center" spacing={1}>
                  <Typography variant="body2" color="text.secondary" sx={{ minWidth: 22 }}>
                    {index + 1}.
                  </Typography>
                  <Stack sx={{ flex: 1, minWidth: 0 }} spacing={0.5}>
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Typography variant="body2" fontWeight={700} noWrap>
                        {item.merchant_name}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {item.receipt_count} receipts
                      </Typography>
                      <Typography variant="body2" fontWeight={700} sx={{ marginLeft: "auto" }}>
                        {formatCurrency(value, filters.currency)}
                      </Typography>
                    </Stack>
                    <Box
                      sx={{
                        position: "relative",
                        height: 10,
                        borderRadius: 999,
                        backgroundColor: "#F4E7D7",
                        overflow: "hidden",
                        border: "1px solid #E3D4C1"
                      }}
                    >
                      <Box
                        sx={{
                          position: "absolute",
                          inset: 0,
                          width: `${widthPercent}%`,
                          background: "linear-gradient(90deg, #BC6C25 0%, #D3923D 100%)",
                          borderRadius: 999
                        }}
                      />
                    </Box>
                  </Stack>
                </Stack>
              );
            })}
          </Stack>
        )}
      </Box>
    </Paper>
  );
};

export default TopMerchantsBarChart;
