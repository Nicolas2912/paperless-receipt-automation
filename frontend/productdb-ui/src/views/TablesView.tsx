import { useMemo, useState } from "react";
import {
  Box,
  FormControl,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TablePagination,
  TableRow,
  Typography
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { fetchTableRows } from "../api/client";
import { humaniseKey } from "../utils/format";

const TABLES = [
  "addresses",
  "merchants",
  "files",
  "texts",
  "receipts",
  "receipt_items",
  "extraction_runs"
];

const TablesView = () => {
  const [selectedTable, setSelectedTable] = useState<string>(TABLES[0]);
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(25);

  const tableQuery = useQuery({
    queryKey: ["table", selectedTable, page, rowsPerPage],
    queryFn: () => fetchTableRows(selectedTable, rowsPerPage, page * rowsPerPage)
  });

  const columns = useMemo(() => {
    const items = tableQuery.data?.items ?? [];
    const keys = new Set<string>();
    items.forEach((item) => {
      Object.keys(item).forEach((key) => keys.add(key));
    });
    return Array.from(keys);
  }, [tableQuery.data?.items]);

  return (
    <Stack spacing={3}>
      <Paper elevation={0} sx={{ p: 2.5 }}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
          <FormControl size="small" sx={{ minWidth: 200 }}>
            <InputLabel id="table-select-label">Table</InputLabel>
            <Select
              labelId="table-select-label"
              value={selectedTable}
              label="Table"
              onChange={(event) => {
                setSelectedTable(event.target.value);
                setPage(0);
              }}
            >
              {TABLES.map((table) => (
                <MenuItem key={table} value={table}>
                  {humaniseKey(table)}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <Box sx={{ display: "flex", alignItems: "center" }}>
            <Typography variant="body2" color="text.secondary">
              {tableQuery.data?.total ?? 0} rows total
            </Typography>
          </Box>
        </Stack>
      </Paper>

      <Paper elevation={0}>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                {columns.map((col) => (
                  <TableCell key={col}>{humaniseKey(col)}</TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {(tableQuery.data?.items ?? []).map((row, index) => (
                <TableRow key={index}>
                  {columns.map((col) => {
                    const value = row[col];
                    let display: string;
                    if (value === null || value === undefined) {
                      display = "–";
                    } else if (typeof value === "string" && value.length > 120) {
                      display = `${value.slice(0, 120)}…`;
                    } else {
                      display = String(value);
                    }
                    return (
                      <TableCell key={col}>
                        <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
                          {display}
                        </Typography>
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}

              {(tableQuery.data?.items ?? []).length === 0 && (
                <TableRow>
                  <TableCell colSpan={columns.length || 1} align="center" sx={{ py: 6 }}>
                    <Typography color="text.secondary">No rows to display.</Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
        <TablePagination
          component="div"
          count={tableQuery.data?.total ?? 0}
          page={page}
          onPageChange={(_, newPage) => setPage(newPage)}
          rowsPerPage={rowsPerPage}
          onRowsPerPageChange={(event) => {
            setRowsPerPage(parseInt(event.target.value, 10));
            setPage(0);
          }}
          rowsPerPageOptions={[10, 25, 50, 100, 200]}
        />
      </Paper>
    </Stack>
  );
};

export default TablesView;
