import { useMemo, useState } from "react";
import {
  Chip,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
  InputAdornment
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import { useQuery } from "@tanstack/react-query";
import { fetchMerchants } from "../api/client";
import { formatCurrency } from "../utils/format";

const MerchantsView = () => {
  const merchantsQuery = useQuery({ queryKey: ["merchants"], queryFn: fetchMerchants });
  const [searchTerm, setSearchTerm] = useState("");

  const filteredMerchants = useMemo(() => {
    const list = merchantsQuery.data?.items ?? [];
    if (!searchTerm) {
      return list;
    }
    const lower = searchTerm.toLowerCase();
    return list.filter((merchant) => merchant.merchant_name.toLowerCase().includes(lower));
  }, [merchantsQuery.data?.items, searchTerm]);

  return (
    <Stack spacing={3}>
      <Paper elevation={0} sx={{ p: 2.5 }}>
        <TextField
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
          placeholder="Search merchants"
          size="small"
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            )
          }}
          sx={{ width: { xs: "100%", sm: 320 } }}
        />
      </Paper>

      <Paper elevation={0}>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Merchant</TableCell>
                <TableCell>Location</TableCell>
                <TableCell align="right">Receipts</TableCell>
                <TableCell align="right">Total Gross</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {filteredMerchants.map((merchant) => (
                <TableRow key={merchant.merchant_id}>
                  <TableCell>
                    <Typography fontWeight={600}>{merchant.merchant_name}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      ID: {merchant.merchant_id}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2" color="text.secondary">
                      {merchant.city ?? "Unknown city"}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {merchant.country ?? "Unknown country"}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Chip label={`${merchant.receipt_count} receipts`} size="small" color="primary" variant="outlined" />
                  </TableCell>
                  <TableCell align="right">
                    {formatCurrency(merchant.total_gross_cents)}
                  </TableCell>
                </TableRow>
              ))}

              {filteredMerchants.length === 0 && (
                <TableRow>
                  <TableCell colSpan={4} align="center" sx={{ py: 6 }}>
                    <Typography color="text.secondary">No merchants found.</Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
    </Stack>
  );
};

export default MerchantsView;
