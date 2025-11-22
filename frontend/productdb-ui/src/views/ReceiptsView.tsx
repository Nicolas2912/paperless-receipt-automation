import { useMemo, useState } from "react";
import {
  Autocomplete,
  Box,
  Drawer,
  Grid,
  IconButton,
  InputAdornment,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  TableSortLabel,
  TextField,
  Tooltip,
  Typography
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import ClearIcon from "@mui/icons-material/Clear";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import { useQuery } from "@tanstack/react-query";
import PlaceholderCard from "../components/PlaceholderCard";
import { GlobalFilters } from "../components/GlobalFilterBar";
import {
  fetchMerchants,
  fetchReceiptDetail,
  fetchReceipts,
  MerchantOverviewItem,
  ReceiptOverviewItem
} from "../api/client";
import { formatCurrency, formatDateTime, formatPercent } from "../utils/format";

interface ReceiptsViewProps {
  filters: GlobalFilters;
}

interface SortState {
  sort: "purchase_date_time" | "total_gross" | "merchant" | "item_count";
  direction: "asc" | "desc";
}

const ReceiptsView = ({ filters }: ReceiptsViewProps) => {
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(15);
  const [searchTerm, setSearchTerm] = useState(filters.search ?? "");
  const [selectedMerchant, setSelectedMerchant] = useState<MerchantOverviewItem | null>(null);
  const [sortState, setSortState] = useState<SortState>({ sort: "purchase_date_time", direction: "desc" });
  const [selectedReceipt, setSelectedReceipt] = useState<ReceiptOverviewItem | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const merchantsQuery = useQuery({ queryKey: ["merchants"], queryFn: fetchMerchants });

  const receiptsQuery = useQuery({
    queryKey: [
      "receipts",
      {
        page,
        limit: rowsPerPage,
        search: searchTerm.trim() || filters.search || undefined,
        merchantId: selectedMerchant?.merchant_id,
        sort: sortState.sort,
        direction: sortState.direction
      }
    ],
    queryFn: () =>
      fetchReceipts({
        page,
        limit: rowsPerPage,
        search: searchTerm.trim() || filters.search || undefined,
        merchantId: selectedMerchant?.merchant_id,
        sort: sortState.sort,
        direction: sortState.direction
      })
  });

  const detailQuery = useQuery({
    queryKey: ["receipt-detail", selectedReceipt?.receipt_id],
    enabled: drawerOpen && Boolean(selectedReceipt?.receipt_id),
    queryFn: () => fetchReceiptDetail(selectedReceipt!.receipt_id)
  });

  const merchantsOptions = useMemo(() => merchantsQuery.data?.items ?? [], [merchantsQuery.data?.items]);

  const handleSort = (property: SortState["sort"]) => {
    setSortState((prev) => {
      if (prev.sort === property) {
        return {
          sort: property,
          direction: prev.direction === "asc" ? "desc" : "asc"
        };
      }
      return { sort: property, direction: "desc" };
    });
  };

  const handleRowClick = (row: ReceiptOverviewItem) => {
    setSelectedReceipt(row);
    setDrawerOpen(true);
  };

  const detailPanel = selectedReceipt ? (
    <Stack spacing={2} sx={{ p: 1 }}>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" color="text.secondary">
          {formatDateTime(selectedReceipt.purchase_date_time)}
        </Typography>
        <Typography variant="h6" fontWeight={800}>
          {selectedReceipt.merchant_name}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {selectedReceipt.merchant_city ?? "Unknown city"}
        </Typography>
        <Typography variant="subtitle1" fontWeight={800} sx={{ mt: 1 }}>
          {formatCurrency((selectedReceipt.total_gross ?? 0) * 100)}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Payment: {selectedReceipt.payment_method || "Unknown"} • Items: {selectedReceipt.item_count}
        </Typography>
      </Paper>
      <PlaceholderCard
        title="ReceiptSummary"
        subtitle="Header info for the receipt"
        height={120}
      />
      <PlaceholderCard
        title="ReceiptItemsChart"
        subtitle="Donut or bars by line_gross share"
        height={160}
      />
      <PlaceholderCard
        title="RawReceiptPreview"
        subtitle="Image or text preview of the receipt"
        height={160}
      />
    </Stack>
  ) : (
    <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
      <Typography color="text.secondary">Select a receipt to see details.</Typography>
    </Paper>
  );

  return (
    <Stack spacing={2}>
      <Paper elevation={0} sx={{ p: 2.5, border: "1px solid #E3D4C1" }}>
        <Typography variant="h6" fontWeight={800} sx={{ mb: 0.5 }}>
          Receipts drill-down
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Left: receipts table. Right: detail with summary, items chart, and raw preview. Drawer opens on row click.
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Filters: {filters.timeRange} • {filters.currency}
        </Typography>
      </Paper>

      <Paper elevation={0} sx={{ p: 2.5, border: "1px solid #E3D4C1" }}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
          <TextField
            placeholder="Search receipts (merchant, date, currency, payment method)"
            value={searchTerm}
            onChange={(event) => {
              setPage(0);
              setSearchTerm(event.target.value);
            }}
            size="small"
            sx={{ width: { xs: "100%", md: 320 } }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon fontSize="small" />
                </InputAdornment>
              ),
              endAdornment: searchTerm ? (
                <InputAdornment position="end">
                  <IconButton size="small" onClick={() => setSearchTerm("")}>
                    <ClearIcon fontSize="small" />
                  </IconButton>
                </InputAdornment>
              ) : undefined
            }}
          />

          <Autocomplete
            options={merchantsOptions}
            getOptionLabel={(option) => option.merchant_name}
            value={selectedMerchant}
            onChange={(_, value) => {
              setSelectedMerchant(value);
              setPage(0);
            }}
            sx={{ width: { xs: "100%", md: 260 } }}
            renderInput={(params) => <TextField {...params} size="small" label="Merchant" />}
            isOptionEqualToValue={(option, value) => option.merchant_id === value.merchant_id}
          />

          <Tooltip title="Reset filters">
            <IconButton
              onClick={() => {
                setSelectedMerchant(null);
                setSearchTerm("");
                setSortState({ sort: "purchase_date_time", direction: "desc" });
                setPage(0);
              }}
            >
              <RestartAltIcon />
            </IconButton>
          </Tooltip>
        </Stack>
      </Paper>

      <Grid container spacing={2} alignItems="stretch">
        <Grid item xs={12} md={7}>
          <Paper elevation={0} sx={{ border: "1px solid #E3D4C1" }}>
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell sortDirection={sortState.sort === "purchase_date_time" ? sortState.direction : false}>
                      <TableSortLabel
                        active={sortState.sort === "purchase_date_time"}
                        direction={sortState.sort === "purchase_date_time" ? sortState.direction : "asc"}
                        onClick={() => handleSort("purchase_date_time")}
                      >
                        Purchase Date
                      </TableSortLabel>
                    </TableCell>
                    <TableCell>Merchant</TableCell>
                    <TableCell>Payment</TableCell>
                    <TableCell sortDirection={sortState.sort === "total_gross" ? sortState.direction : false}>
                      <TableSortLabel
                        active={sortState.sort === "total_gross"}
                        direction={sortState.sort === "total_gross" ? sortState.direction : "asc"}
                        onClick={() => handleSort("total_gross")}
                      >
                        Total Gross
                      </TableSortLabel>
                    </TableCell>
                    <TableCell sortDirection={sortState.sort === "item_count" ? sortState.direction : false}>
                      <TableSortLabel
                        active={sortState.sort === "item_count"}
                        direction={sortState.sort === "item_count" ? sortState.direction : "asc"}
                        onClick={() => handleSort("item_count")}
                      >
                        Items
                      </TableSortLabel>
                    </TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {(receiptsQuery.data?.items ?? []).map((row) => (
                    <TableRow
                      key={row.receipt_id}
                      hover
                      selected={row.receipt_id === selectedReceipt?.receipt_id}
                      onClick={() => handleRowClick(row)}
                      sx={{ cursor: "pointer" }}
                    >
                      <TableCell>{formatDateTime(row.purchase_date_time)}</TableCell>
                      <TableCell>
                        <Typography fontWeight={700}>{row.merchant_name}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {row.merchant_city ?? "Unknown city"}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2">{row.payment_method || "Unknown"}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Currency: {row.currency}
                        </Typography>
                      </TableCell>
                      <TableCell>{formatCurrency((row.total_gross ?? 0) * 100)}</TableCell>
                      <TableCell>{row.item_count}</TableCell>
                    </TableRow>
                  ))}
                  {(receiptsQuery.data?.items ?? []).length === 0 && (
                    <TableRow>
                      <TableCell colSpan={5} align="center" sx={{ py: 4 }}>
                        <Typography color="text.secondary">No receipts found.</Typography>
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
            <TablePagination
              component="div"
              count={receiptsQuery.data?.total ?? 0}
              page={page}
              onPageChange={(_, newPage) => setPage(newPage)}
              rowsPerPage={rowsPerPage}
              onRowsPerPageChange={(event) => {
                setRowsPerPage(parseInt(event.target.value, 10));
                setPage(0);
              }}
              rowsPerPageOptions={[10, 15, 25, 50]}
            />
          </Paper>
        </Grid>

        <Grid item xs={12} md={5}>
          {detailPanel}
        </Grid>
      </Grid>

      <Drawer anchor="right" open={drawerOpen} onClose={() => setDrawerOpen(false)}>
        <Box sx={{ width: 360, p: 2 }}>
          {drawerOpen && detailQuery.data ? (
            <Stack spacing={1}>
              {detailPanel}
              <Paper variant="outlined" sx={{ p: 2 }}>
                <Typography variant="subtitle2" color="text.secondary">
                  Items preview
                </Typography>
                {detailQuery.data.items.slice(0, 5).map((item) => (
                  <Typography key={item.item_id} variant="body2">
                    {item.product_name} • {formatCurrency((item.line_gross ?? 0) * 100)} • Tax {formatPercent(item.tax_rate / 100)}
                  </Typography>
                ))}
                {detailQuery.data.items.length === 0 && (
                  <Typography variant="body2" color="text.secondary">
                    No items recorded.
                  </Typography>
                )}
              </Paper>
            </Stack>
          ) : (
            detailPanel
          )}
        </Box>
      </Drawer>
    </Stack>
  );
};

export default ReceiptsView;
