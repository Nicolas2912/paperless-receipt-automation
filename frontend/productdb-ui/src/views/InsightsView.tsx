import { Grid, Paper, Stack, Typography } from "@mui/material";
import PlaceholderCard from "../components/PlaceholderCard";
import { GlobalFilters } from "../components/GlobalFilterBar";

interface InsightsViewProps {
  filters: GlobalFilters;
}

const InsightsView = ({ filters }: InsightsViewProps) => {
  return (
    <Stack spacing={2}>
      <Paper elevation={0} sx={{ p: 2.5, border: "1px solid #E3D4C1" }}>
        <Typography variant="h6" fontWeight={800} sx={{ mb: 0.5 }}>
          Insights
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Wider behavioural charts: time-of-day, distributions, net vs tax decomposition. Full-width cards or 2×2 blocks.
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Filters: {filters.timeRange} • {filters.currency}
        </Typography>
      </Paper>

      <Grid container spacing={2}>
        <Grid item xs={12}>
          <PlaceholderCard
            title="DayOfWeekHourHeatmap"
            subtitle="Spend by weekday × hour"
            height={220}
          />
        </Grid>
        <Grid item xs={12} md={6}>
          <PlaceholderCard
            title="ReceiptTotalsHistogram"
            subtitle="Distribution of gross totals"
            height={200}
          />
        </Grid>
        <Grid item xs={12} md={6}>
          <PlaceholderCard
            title="PfandBalance"
            subtitle="Line: net Pfand over time"
            height={200}
          />
        </Grid>
        <Grid item xs={12}>
          <PlaceholderCard
            title="NetVsTaxStackedBar"
            subtitle="Monthly net + tax = gross"
            height={200}
          />
        </Grid>
      </Grid>
    </Stack>
  );
};

export default InsightsView;
