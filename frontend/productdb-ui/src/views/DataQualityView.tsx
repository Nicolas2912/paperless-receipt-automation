import { useState } from "react";
import {
  Paper,
  Stack,
  Typography,
  Grid,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TablePagination
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import PlaceholderCard from "../components/PlaceholderCard";
import { GlobalFilters } from "../components/GlobalFilterBar";
import { fetchTableRows } from "../api/client";

interface DataQualityViewProps {
  filters: GlobalFilters;
}

const DataQualityView = ({ filters }: DataQualityViewProps) => {
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(10);

  const extractionRunsQuery = useQuery({
    queryKey: ["extraction-runs", page, rowsPerPage],
    queryFn: () => fetchTableRows("extraction_runs", rowsPerPage, page * rowsPerPage)
  });

  const runs = extractionRunsQuery.data?.items ?? [];
  const total = extractionRunsQuery.data?.total ?? 0;

  return (
    <Stack spacing={2}>
      <Paper elevation={0} sx={{ p: 2.5, border: "1px solid #e2e8f0" }}>
        <Typography variant="h6" fontWeight={800} sx={{ mb: 0.5 }}>
          Data Quality
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Monitoring extraction runs and pipeline health. Use the placeholders below for charts, plus an actual run table.
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Filters: {filters.timeRange} • {filters.currency}
        </Typography>
      </Paper>

      <Grid container spacing={2}>
        <Grid item xs={12} md={6}>
          <PlaceholderCard
            title="ExtractionRunsOverTime"
            subtitle="Stacked status counts"
            height={200}
          />
        </Grid>
        <Grid item xs={12} md={6}>
          <PlaceholderCard
            title="RunDurationBox"
            subtitle="Duration distribution"
            height={200}
          />
        </Grid>
      </Grid>

      <Paper elevation={0} sx={{ border: "1px solid #e2e8f0" }}>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>run_id</TableCell>
                <TableCell>model_name</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Started</TableCell>
                <TableCell>Finished</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {runs.map((run, index) => (
                <TableRow key={index}>
                  <TableCell>{run.run_id as number}</TableCell>
                  <TableCell>{(run.model_name as string) ?? "–"}</TableCell>
                  <TableCell>{(run.status as string) ?? "–"}</TableCell>
                  <TableCell>{(run.started_at as string) ?? "–"}</TableCell>
                  <TableCell>{(run.finished_at as string) ?? "–"}</TableCell>
                </TableRow>
              ))}
              {runs.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} align="center">
                    <Typography color="text.secondary">No runs recorded yet.</Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
        <TablePagination
          component="div"
          count={total}
          page={page}
          onPageChange={(_, p) => setPage(p)}
          rowsPerPage={rowsPerPage}
          onRowsPerPageChange={(event) => {
            setRowsPerPage(parseInt(event.target.value, 10));
            setPage(0);
          }}
          rowsPerPageOptions={[10, 25, 50]}
        />
      </Paper>
    </Stack>
  );
};

export default DataQualityView;
