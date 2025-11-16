import { useMemo, useState } from "react";
import {
  Autocomplete,
  Box,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
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
  Toolbar,
  Tooltip,
  Typography,
  Button,
  CircularProgress,
  Accordion,
  AccordionSummary,
  AccordionDetails
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import SearchIcon from "@mui/icons-material/Search";
import ClearIcon from "@mui/icons-material/Clear";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import { useQuery } from "@tanstack/react-query";
import {
  fetchMerchants,
  fetchReceiptDetail,
  fetchReceipts,
  MerchantOverviewItem,
  ReceiptOverviewItem
} from "../api/client";
import { formatCurrency, formatDateTime, formatPercent } from "../utils/format";

interface SortState {
  sort: "purchase_date_time" | "total_gross" | "merchant" | "item_count";
  direction: "asc" | "desc";
}

const ReceiptsView = () => {
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(25);
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedMerchant, setSelectedMerchant] = useState<MerchantOverviewItem | null>(null);
  const [sortState, setSortState] = useState<SortState>({ sort: "purchase_date_time", direction: "desc" });
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedReceipt, setSelectedReceipt] = useState<ReceiptOverviewItem | null>(null);

  const merchantsQuery = useQuery({ queryKey: ["merchants"], queryFn: fetchMerchants });

  const receiptsQuery = useQuery({
    queryKey: [
      "receipts",
      {
        page,
        limit: rowsPerPage,
        search: searchTerm.trim() || undefined,
        merchantId: selectedMerchant?.merchant_id,
        sort: sortState.sort,
        direction: sortState.direction
      }
    ],
    queryFn: () =>
      fetchReceipts({
        page,
        limit: rowsPerPage,
        search: searchTerm.trim() || undefined,
        merchantId: selectedMerchant?.merchant_id,
        sort: sortState.sort,
        direction: sortState.direction
      })
  });

  const detailQuery = useQuery({
    queryKey: ["receipt-detail", selectedReceipt?.receipt_id],
    enabled: detailOpen && Boolean(selectedReceipt?.receipt_id),
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
    setDetailOpen(true);
  };

  const closeDetail = () => {
    setDetailOpen(false);
  };

  return (
    <Stack spacing={3}>
      <Paper elevation={0} sx={{ p: 2.5 }}>
        <Toolbar disableGutters sx={{ gap: 2, flexWrap: "wrap" }}>
          <TextField
            placeholder="Search receipts (merchant, date, currency, payment method)"
            value={searchTerm}
            onChange={(event) => {
              setPage(0);
              setSearchTerm(event.target.value);
            }}
            size="small"
            sx={{ width: { xs: "100%", md: 340 } }}
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
        </Toolbar>
      </Paper>

      <Paper elevation={0}>
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
                <TableCell sortDirection={sortState.sort === "merchant" ? sortState.direction : false}>
                  <TableSortLabel
                    active={sortState.sort === "merchant"}
                    direction={sortState.sort === "merchant" ? sortState.direction : "asc"}
                    onClick={() => handleSort("merchant")}
                  >
                    Location
                  </TableSortLabel>
                </TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {receiptsQuery.isLoading && (
                <TableRow>
                  <TableCell colSpan={6} align="center" sx={{ py: 6 }}>
                    <CircularProgress size={28} />
                  </TableCell>
                </TableRow>
              )}

              {receiptsQuery.isError && (
                <TableRow>
                  <TableCell colSpan={6} align="center" sx={{ py: 6 }}>
                    <Typography color="error">Failed to load receipts.</Typography>
                  </TableCell>
                </TableRow>
              )}

              {receiptsQuery.data?.items.length === 0 && !receiptsQuery.isLoading && (
                <TableRow>
                  <TableCell colSpan={6} align="center" sx={{ py: 6 }}>
                    <Typography color="text.secondary">No receipts found.</Typography>
                  </TableCell>
                </TableRow>
              )}

              {receiptsQuery.data?.items.map((row) => (
                <TableRow
                  key={row.receipt_id}
                  hover
                  sx={{ cursor: "pointer" }}
                  onClick={() => handleRowClick(row)}
                >
                  <TableCell>{formatDateTime(row.purchase_date_time)}</TableCell>
                  <TableCell>
                    <Stack spacing={0.5}>
                      <Typography fontWeight={600}>{row.merchant_name}</Typography>
                      <Typography variant="body2" color="text.secondary">
                        {row.merchant_city ?? "Unknown city"} · {row.merchant_country ?? "Unknown country"}
                      </Typography>
                    </Stack>
                  </TableCell>
                  <TableCell>
                    <Chip size="small" label={row.payment_method} />
                  </TableCell>
                  <TableCell>{formatCurrency(row.total_gross, row.currency)}</TableCell>
                  <TableCell>{row.item_count}</TableCell>
                  <TableCell>
                    <Typography variant="body2" color="text.secondary">
                      {row.merchant_city ?? "–"}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
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
          rowsPerPageOptions={[10, 25, 50, 100]}
        />
      </Paper>

      <Dialog
        open={detailOpen}
        onClose={closeDetail}
        fullWidth
        maxWidth="md"
        scroll="paper"
      >
        <DialogTitle>Receipt details</DialogTitle>
        <DialogContent dividers>
          {detailQuery.isLoading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
              <CircularProgress />
            </Box>
          )}

          {detailQuery.isError && (
            <Typography color="error">Failed to load receipt.</Typography>
          )}

          {detailQuery.data && (
            <Stack spacing={3}>
              <Box>
                <Typography variant="h6">{detailQuery.data.merchant_name}</Typography>
                <Typography variant="body2" color="text.secondary">
                  {detailQuery.data.street ?? ""} {detailQuery.data.city ?? ""} {detailQuery.data.country ?? ""}
                </Typography>
              </Box>

              <Grid container spacing={2}>
                <Grid item xs={12} sm={6}>
                  <Paper variant="outlined" sx={{ p: 2 }}>
                    <Typography variant="subtitle2" color="text.secondary">
                      Purchase date
                    </Typography>
                    <Typography fontWeight={600}>
                      {formatDateTime(detailQuery.data.purchase_date_time)}
                    </Typography>
                    <Divider sx={{ my: 1 }} />
                    <Typography variant="subtitle2" color="text.secondary">
                      Payment method
                    </Typography>
                    <Chip label={detailQuery.data.payment_method} />
                  </Paper>
                </Grid>
                <Grid item xs={12} sm={6}>
                  <Paper variant="outlined" sx={{ p: 2 }}>
                    <Typography variant="subtitle2" color="text.secondary">
                      Totals
                    </Typography>
                    <Typography fontWeight={600}>
                      Gross: {formatCurrency(detailQuery.data.total_gross, detailQuery.data.currency)}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Net: {formatCurrency(detailQuery.data.total_net, detailQuery.data.currency)}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      Tax: {formatCurrency(detailQuery.data.total_tax, detailQuery.data.currency)}
                    </Typography>
                  </Paper>
                </Grid>
              </Grid>

              <Paper variant="outlined">
                <TableContainer>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>Product</TableCell>
                        <TableCell>Type</TableCell>
                        <TableCell align="right">Quantity</TableCell>
                        <TableCell align="right">Unit Net</TableCell>
                        <TableCell align="right">Unit Gross</TableCell>
                        <TableCell align="right">Tax</TableCell>
                        <TableCell align="right">Line Gross</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {detailQuery.data.items.map((item) => (
                        <TableRow key={item.item_id}>
                          <TableCell>
                            <Stack spacing={0.5}>
                              <Typography fontWeight={600}>{item.product_name}</Typography>
                            </Stack>
                          </TableCell>
                          <TableCell>
                            <Chip size="small" label={item.line_type} variant="outlined" />
                          </TableCell>
                          <TableCell align="right">{item.quantity}</TableCell>
                          <TableCell align="right">
                            {formatCurrency(item.unit_price_net, detailQuery.data.currency)}
                          </TableCell>
                          <TableCell align="right">
                            {formatCurrency(item.unit_price_gross, detailQuery.data.currency)}
                          </TableCell>
                          <TableCell align="right">{formatPercent(item.tax_rate)}</TableCell>
                          <TableCell align="right">
                            {formatCurrency(item.line_gross, detailQuery.data.currency)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              </Paper>

              {detailQuery.data.extraction_runs.length > 0 && (
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                    Extraction Runs
                  </Typography>
                  <Stack spacing={1.5}>
                    {detailQuery.data.extraction_runs.map((run) => (
                      <Stack key={run.run_id} direction="row" justifyContent="space-between" alignItems="center">
                        <Box>
                          <Typography fontWeight={600}>{run.model_name}</Typography>
                          <Typography variant="body2" color="text.secondary">
                            Started {formatDateTime(run.started_at)} · Finished {formatDateTime(run.finished_at)}
                          </Typography>
                        </Box>
                        <Chip label={run.status ?? "OK"} color="success" variant="outlined" />
                      </Stack>
                    ))}
                  </Stack>
                </Paper>
              )}

              {detailQuery.data.raw_content && (
                <Accordion>
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Typography>Raw Payload</Typography>
                  </AccordionSummary>
                  <AccordionDetails>
                    <Box component="pre" sx={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
                      {detailQuery.data.raw_content}
                    </Box>
                  </AccordionDetails>
                </Accordion>
              )}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={closeDetail}>Close</Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
};

export default ReceiptsView;
