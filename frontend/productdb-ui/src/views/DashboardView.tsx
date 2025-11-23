import { Grid, Paper, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PlaceholderCard from "../components/PlaceholderCard";
import { GlobalFilters } from "../components/GlobalFilterBar";
import { fetchSummary } from "../api/client";
import { formatCurrency } from "../utils/format";
import MonthlySpendLineChart from "../components/MonthlySpendLineChart";
import PaymentMethodDonut from "../components/PaymentMethodDonut";
import TaxRateSplitChart from "../components/TaxRateSplitChart";

interface DashboardViewProps {
  filters: GlobalFilters;
}

const DashboardView = ({ filters }: DashboardViewProps) => {
  const summaryQuery = useQuery({
    queryKey: ["summary", filters.timeRange],
    queryFn: () => fetchSummary()
  });

  const summary = summaryQuery.data;

  return (
    <Stack spacing={2}>
      <Paper elevation={0} sx={{ p: 2.5, border: "1px solid #E3D4C1" }}>
        <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" spacing={2}>
          <Stack spacing={0.5}>
            <Typography variant="h6" fontWeight={800}>
              Dashboard overview
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Global filters above apply to every chart. Layout follows the 2×2 grid: time-series first, then splits and rankings.
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Filters: {filters.timeRange} • Currency {filters.currency} • Merchant {filters.merchantId ? `#${filters.merchantId}` : "All"}
            </Typography>
          </Stack>
          <Stack direction={{ xs: "column", md: "row" }} spacing={1}>
            <Paper variant="outlined" sx={{ p: 1.5, minWidth: 160 }}>
              <Typography variant="caption" color="text.secondary">
                Total gross
              </Typography>
              <Typography variant="h6" fontWeight={800}>
                {summary ? formatCurrency(summary.totals.total_gross_cents) : "—"}
              </Typography>
            </Paper>
            <Paper variant="outlined" sx={{ p: 1.5, minWidth: 160 }}>
              <Typography variant="caption" color="text.secondary">
                Receipts
              </Typography>
              <Typography variant="h6" fontWeight={800}>
                {summary?.counts?.receipts ?? "—"}
              </Typography>
            </Paper>
          </Stack>
        </Stack>
      </Paper>

      <Grid container spacing={2}>
        <Grid item xs={12} lg={7}>
          <MonthlySpendLineChart filters={filters} />
        </Grid>
        <Grid item xs={12} lg={5}>
          <Grid container spacing={2}>
            <Grid item xs={12} md={6} lg={12}>
              <PaymentMethodDonut filters={filters} height={260} />
            </Grid>
            <Grid item xs={12} md={6} lg={12}>
              <TaxRateSplitChart filters={filters} height={260} />
            </Grid>
          </Grid>
        </Grid>
        <Grid item xs={12} md={6}>
          <PlaceholderCard
            title="TopMerchantsBarChart"
            subtitle="Top 10 merchants by spend."
            height={220}
          />
        </Grid>
        <Grid item xs={12} md={6}>
          <PlaceholderCard
            title="CalendarDailySpendHeatmap"
            subtitle="Intensity = total_gross per day."
            height={220}
          />
        </Grid>
      </Grid>
    </Stack>
  );
};

export default DashboardView;
