import { useMemo, useState } from "react";
import {
  Box,
  Drawer,
  Grid,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PlaceholderCard from "../components/PlaceholderCard";
import { GlobalFilters } from "../components/GlobalFilterBar";
import { fetchMerchants } from "../api/client";
import { formatCurrency } from "../utils/format";

interface MerchantsViewProps {
  filters: GlobalFilters;
}

const MerchantsView = ({ filters }: MerchantsViewProps) => {
  const merchantsQuery = useQuery({ queryKey: ["merchants"], queryFn: fetchMerchants });
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedMerchantId, setSelectedMerchantId] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const merchants = merchantsQuery.data?.items ?? [];

  const filteredMerchants = useMemo(() => {
    if (!searchTerm) {
      return merchants;
    }
    const lower = searchTerm.toLowerCase();
    return merchants.filter((merchant) => merchant.merchant_name.toLowerCase().includes(lower));
  }, [merchants, searchTerm]);

  const selectedMerchant = merchants.find((m) => m.merchant_id === selectedMerchantId) ?? null;

  const handleSelect = (merchantId: number) => {
    setSelectedMerchantId(merchantId);
    setDrawerOpen(true);
  };

  const merchantSummary = (
    <Paper elevation={0} sx={{ p: 2.5, border: "1px solid #e2e8f0", mb: 2 }}>
      <Typography variant="h6" fontWeight={800} sx={{ mb: 0.5 }}>
        Merchants master–detail
      </Typography>
      <Typography variant="body2" color="text.secondary">
        Left: table with basic info. Right: spend over time, category split, and latest receipts for the selected merchant.
      </Typography>
      <Typography variant="caption" color="text.secondary">
        Filters: {filters.timeRange} • {filters.currency} • {filters.search || "No search"}
      </Typography>
    </Paper>
  );

  const detailPanel = selectedMerchant ? (
    <Stack spacing={2} sx={{ p: 1 }}>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" color="text.secondary">
          Selected merchant
        </Typography>
        <Typography variant="h6" fontWeight={800}>
          {selectedMerchant.merchant_name}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {selectedMerchant.city ?? "Unknown city"} • {selectedMerchant.country ?? "Unknown country"}
        </Typography>
        <Typography variant="body2" sx={{ mt: 1 }}>
          Receipts: {selectedMerchant.receipt_count} • Total gross: {formatCurrency(selectedMerchant.total_gross_cents)}
        </Typography>
      </Paper>
      <PlaceholderCard
        title="MerchantSpendOverTime"
        subtitle="Line: sum per month for selected merchant"
        height={160}
      />
      <PlaceholderCard
        title="MerchantCategorySplit"
        subtitle="Split by tax_rate or merchant type"
        height={160}
      />
      <PlaceholderCard
        title="MerchantReceiptList"
        subtitle="Last N receipts for this merchant"
        height={220}
      />
    </Stack>
  ) : (
    <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
      <Typography color="text.secondary">Select a merchant to see charts.</Typography>
    </Paper>
  );

  return (
    <Stack spacing={2}>
      {merchantSummary}
      <Grid container spacing={2} alignItems="stretch">
        <Grid item xs={12} md={6} lg={5}>
          <Paper elevation={0} sx={{ p: 2.5, height: "100%", border: "1px solid #e2e8f0" }}>
            <TextField
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              size="small"
              fullWidth
              placeholder="Search merchants"
              sx={{ mb: 2 }}
            />
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Name</TableCell>
                    <TableCell align="right">Receipts</TableCell>
                    <TableCell align="right">Total Gross</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {filteredMerchants.map((merchant) => (
                    <TableRow
                      key={merchant.merchant_id}
                      hover
                      selected={merchant.merchant_id === selectedMerchantId}
                      onClick={() => handleSelect(merchant.merchant_id)}
                      sx={{ cursor: "pointer" }}
                    >
                      <TableCell>
                        <Typography fontWeight={700}>{merchant.merchant_name}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {merchant.city ?? "Unknown"} • {merchant.country ?? ""}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">{merchant.receipt_count}</TableCell>
                      <TableCell align="right">{formatCurrency(merchant.total_gross_cents)}</TableCell>
                    </TableRow>
                  ))}
                  {filteredMerchants.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={3} align="center">
                        <Typography color="text.secondary">No merchants match your filter.</Typography>
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </Paper>
        </Grid>

        <Grid item xs={12} md={6} lg={7}>
          {detailPanel}
        </Grid>
      </Grid>

      <Drawer anchor="right" open={drawerOpen} onClose={() => setDrawerOpen(false)}>
        <Box sx={{ width: 360, p: 2 }}>{detailPanel}</Box>
      </Drawer>
    </Stack>
  );
};

export default MerchantsView;
