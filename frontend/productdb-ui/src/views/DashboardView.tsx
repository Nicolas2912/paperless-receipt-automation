import {
  Avatar,
  Box,
  Chip,
  CircularProgress,
  Grid,
  Paper,
  Stack,
  Typography
} from "@mui/material";
import StoreIcon from "@mui/icons-material/Store";
import ReceiptIcon from "@mui/icons-material/Receipt";
import InventoryIcon from "@mui/icons-material/Inventory";
import TimelineIcon from "@mui/icons-material/Timeline";
import { useQuery } from "@tanstack/react-query";
import { fetchMerchants, fetchSummary } from "../api/client";
import { formatCurrency, formatDateTime, humaniseKey } from "../utils/format";

const DashboardView = () => {
  const summaryQuery = useQuery({ queryKey: ["summary"], queryFn: fetchSummary });
  const merchantsQuery = useQuery({ queryKey: ["merchants"], queryFn: fetchMerchants });

  if (summaryQuery.isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (summaryQuery.isError || !summaryQuery.data) {
    return <Typography color="error">Unable to load summary data.</Typography>;
  }

  const summary = summaryQuery.data;
  const merchants = merchantsQuery.data?.items ?? [];

  const totalGross = summary.totals.total_gross_cents;
  const totalNet = summary.totals.total_net_cents;
  const totalTax = summary.totals.total_tax_cents;

  const topMerchants = merchants
    .filter((m) => m.total_gross_cents > 0)
    .sort((a, b) => b.total_gross_cents - a.total_gross_cents)
    .slice(0, 5);

  return (
    <Stack spacing={4}>
      <Grid container spacing={3}>
        <Grid item xs={12} md={4}>
          <Paper elevation={0} sx={{ p: 3, height: "100%" }}>
            <Stack direction="row" spacing={2} alignItems="center">
              <Avatar sx={{ bgcolor: "primary.main" }}>
                <ReceiptIcon />
              </Avatar>
              <Box>
                <Typography variant="h6">Receipts</Typography>
                <Typography variant="h4">{summary.counts.receipts ?? 0}</Typography>
                <Typography variant="body2" color="text.secondary">
                  {summary.counts.receipt_items ?? 0} line items captured
                </Typography>
              </Box>
            </Stack>
          </Paper>
        </Grid>
        <Grid item xs={12} md={4}>
          <Paper elevation={0} sx={{ p: 3, height: "100%" }}>
            <Stack direction="row" spacing={2} alignItems="center">
              <Avatar sx={{ bgcolor: "secondary.main" }}>
                <StoreIcon />
              </Avatar>
              <Box>
                <Typography variant="h6">Merchants</Typography>
                <Typography variant="h4">{summary.counts.merchants ?? 0}</Typography>
                <Typography variant="body2" color="text.secondary">
                  {summary.counts.addresses ?? 0} unique locations
                </Typography>
              </Box>
            </Stack>
          </Paper>
        </Grid>
        <Grid item xs={12} md={4}>
          <Paper elevation={0} sx={{ p: 3, height: "100%" }}>
            <Stack direction="row" spacing={2} alignItems="center">
              <Avatar sx={{ bgcolor: "#7c3aed" }}>
                <InventoryIcon />
              </Avatar>
              <Box>
                <Typography variant="h6">Totals</Typography>
                <Typography variant="h4">{formatCurrency(totalGross)}</Typography>
                <Typography variant="body2" color="text.secondary">
                  Net {formatCurrency(totalNet)} &middot; Tax {formatCurrency(totalTax)}
                </Typography>
              </Box>
            </Stack>
          </Paper>
        </Grid>
      </Grid>

      <Grid container spacing={3}>
        <Grid item xs={12} md={6}>
          <Paper elevation={0} sx={{ p: 3, height: "100%" }}>
            <Stack spacing={1.5}>
              <Stack direction="row" spacing={1} alignItems="center">
                <TimelineIcon color="secondary" />
                <Typography variant="h6">Activity Timeline</Typography>
              </Stack>
              <Typography variant="body2" color="text.secondary">
                First purchase: <strong>{formatDateTime(summary.timespan.first_purchase)}</strong>
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Latest purchase: <strong>{formatDateTime(summary.timespan.last_purchase)}</strong>
              </Typography>
            </Stack>
          </Paper>
        </Grid>
        <Grid item xs={12} md={6}>
          <Paper elevation={0} sx={{ p: 3, height: "100%" }}>
            <Typography variant="h6" gutterBottom>
              Top Merchants (by gross spend)
            </Typography>
            <Stack spacing={1.5}>
              {topMerchants.length === 0 && (
                <Typography variant="body2" color="text.secondary">
                  No merchant totals yet.
                </Typography>
              )}
              {topMerchants.map((merchant) => (
                <Stack
                  key={merchant.merchant_id}
                  direction="row"
                  alignItems="center"
                  spacing={2}
                  justifyContent="space-between"
                >
                  <Box>
                    <Typography fontWeight={600}>{merchant.merchant_name}</Typography>
                    <Typography variant="body2" color="text.secondary">
                      {merchant.city ?? "Unknown city"} · {merchant.country ?? "Unknown country"}
                    </Typography>
                  </Box>
                  <Chip label={formatCurrency(merchant.total_gross_cents)} color="secondary" />
                </Stack>
              ))}
            </Stack>
          </Paper>
        </Grid>
      </Grid>

      <Paper elevation={0} sx={{ p: 3 }}>
        <Typography variant="h6" gutterBottom>
          Table Counts
        </Typography>
        <Grid container spacing={2}>
          {Object.entries(summary.counts).map(([key, value]) => (
            <Grid item xs={12} sm={6} md={3} key={key}>
              <Paper elevation={0} sx={{ p: 2, backgroundColor: "#f8fafc" }}>
                <Typography variant="subtitle2" color="text.secondary">
                  {humaniseKey(key)}
                </Typography>
                <Typography variant="h5">{value}</Typography>
              </Paper>
            </Grid>
          ))}
        </Grid>
      </Paper>
    </Stack>
  );
};

export default DashboardView;
