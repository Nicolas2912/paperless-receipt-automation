import { Fragment, useMemo } from "react";
import { Box, Chip, CircularProgress, Paper, Stack, Tooltip, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import axios from "axios";
import dayjs from "dayjs";
import { fetchSpendTimeseries } from "../api/client";
import { formatCurrency } from "../utils/format";
import { resolveDateRange } from "../utils/dateRange";
import { GlobalFilters } from "./GlobalFilterBar";

interface CalendarDailySpendHeatmapProps {
  filters: GlobalFilters;
  height?: number;
}

type DayCell = {
  date: dayjs.Dayjs;
  value: number;
  count: number;
  inRange: boolean;
};

const COLOR_SCALE = ["#F4E7D7", "#E2E8C9", "#C8DAA3", "#A6C47A", "#7FA34F"];

const dayLabels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const colorForValue = (value: number, max: number) => {
  if (max <= 0 || value <= 0) {
    return COLOR_SCALE[0];
  }
  const ratio = value / max;
  const idx = Math.min(COLOR_SCALE.length - 1, Math.floor(ratio * (COLOR_SCALE.length - 0.0001)));
  return COLOR_SCALE[idx];
};

const CalendarDailySpendHeatmap = ({ filters, height = 220 }: CalendarDailySpendHeatmapProps) => {
  const range = resolveDateRange(filters.timeRange);
  const query = useQuery({
    queryKey: ["daily-spend-heatmap", range.from, range.to],
    queryFn: () => fetchSpendTimeseries({ dateFrom: range.from, dateTo: range.to })
  });

  const calendar = useMemo(() => {
    const points = query.data?.points ?? [];
    const pointMap = new Map<string, { gross: number; count: number }>();
    points.forEach((point) => {
      pointMap.set(point.date, {
        gross: point.total_gross_cents ?? 0,
        count: point.receipt_count ?? 0
      });
    });

    const latestDate = range.to
      ? dayjs(range.to)
      : points.length > 0
      ? dayjs(points[points.length - 1].date)
      : dayjs();
    const earliestDate = range.from
      ? dayjs(range.from)
      : points.length > 0
      ? dayjs(points[0].date)
      : latestDate.subtract(16, "week");

    const start = earliestDate.startOf("week");
    const end = latestDate.endOf("week");

    const days: DayCell[] = [];
    let cursor = start;
    while (cursor.isBefore(end) || cursor.isSame(end, "day")) {
      const key = cursor.format("YYYY-MM-DD");
      const match = pointMap.get(key);
      const inRange =
        (!range.from || cursor.isSame(range.from, "day") || cursor.isAfter(range.from, "day")) &&
        (!range.to || cursor.isSame(range.to, "day") || cursor.isBefore(range.to, "day"));
      days.push({
        date: cursor,
        value: match?.gross ?? 0,
        count: match?.count ?? 0,
        inRange
      });
      cursor = cursor.add(1, "day");
    }

    const weeks: DayCell[][] = [];
    for (let i = 0; i < days.length; i += 7) {
      weeks.push(days.slice(i, i + 7));
    }

    const inRangeDays = days.filter((day) => day.inRange);
    const maxValue = inRangeDays.reduce((max, day) => Math.max(max, day.value), 0);
    const totalGross = inRangeDays.reduce((sum, day) => sum + day.value, 0);
    const totalReceipts = inRangeDays.reduce((sum, day) => sum + day.count, 0);

    return { weeks, maxValue, totalGross, totalReceipts };
  }, [query.data?.points, range.from, range.to]);

  const hasData = (query.data?.points?.length ?? 0) > 0;

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
            Daily spend heatmap
          </Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip size="small" label={filters.timeRange === "custom" ? "Custom" : "Filtered"} />
            <Typography variant="caption" color="text.secondary">
              {calendar.totalReceipts ?? 0} receipts • {formatCurrency(calendar.totalGross ?? 0, filters.currency)}
            </Typography>
          </Stack>
        </Stack>
      </Stack>

      <Box sx={{ flex: 1, display: "flex", flexDirection: "column", gap: 1, overflow: "hidden" }}>
        {query.isLoading ? (
          <Stack height="100%" alignItems="center" justifyContent="center">
            <CircularProgress size={22} />
          </Stack>
        ) : query.isError ? (
          <Stack spacing={0.5}>
            <Typography variant="subtitle2" fontWeight={700}>
              Could not load heatmap
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {axios.isAxiosError(query.error) ? query.error.message : "Unexpected error"}
            </Typography>
          </Stack>
        ) : !hasData ? (
          <Stack spacing={0.5} justifyContent="center" alignItems="flex-start" sx={{ flex: 1 }}>
            <Typography variant="subtitle2" fontWeight={700}>
              No daily spend yet
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Add receipts or widen the date range.
            </Typography>
          </Stack>
        ) : (
          <>
            <Box
              sx={{
                borderRadius: 2,
                border: "1px dashed #E3D4C1",
                backgroundColor: "#FFFDF8",
                overflow: "hidden"
              }}
            >
              <Box
                sx={{
                  display: "grid",
                  gridTemplateColumns: `28px repeat(${calendar.weeks.length}, minmax(12px, 1fr))`,
                  gridAutoRows: "18px",
                  columnGap: 0.6,
                  rowGap: 0.6,
                  p: 1,
                  minWidth: "100%"
                }}
              >
                <Box />
                {calendar.weeks.map((week, idx) => {
                  const previousInRange = calendar.weeks[idx - 1]?.find((day) => day.inRange);
                  const currentInRange = week.find((day) => day.inRange);
                  const showMonth =
                    currentInRange &&
                    (!previousInRange || currentInRange.date.month() !== previousInRange.date.month());
                  return (
                    <Typography
                      key={`month-${idx}`}
                      variant="caption"
                      textAlign="center"
                      sx={{ lineHeight: "16px" }}
                    >
                      {showMonth ? currentInRange?.date.format("MMM") : ""}
                    </Typography>
                  );
                })}

                {dayLabels.map((label, dayIndex) => (
                  <Fragment key={`row-${label}`}>
                    <Typography
                      variant="caption"
                      color={dayIndex % 2 === 0 ? "text.secondary" : "transparent"}
                      sx={{ lineHeight: "16px" }}
                    >
                      {label}
                    </Typography>
                    {calendar.weeks.map((week, weekIndex) => {
                      const cell = week[dayIndex];
                      const color = cell?.inRange
                        ? colorForValue(cell.value ?? 0, calendar.maxValue ?? 0)
                        : "#F9F3E8";
                      const tooltip = cell?.inRange
                        ? `${cell.date.format("ddd, DD MMM YYYY")}: ${formatCurrency(
                            cell.value ?? 0,
                            filters.currency
                          )} • ${cell.count ?? 0} receipts`
                        : `${cell?.date.format("ddd, DD MMM YYYY")} (outside range)`;
                      return (
                        <Tooltip key={`cell-${weekIndex}-${dayIndex}`} title={tooltip} arrow>
                          <Box
                            sx={{
                              width: "100%",
                              height: "100%",
                              minHeight: 14,
                              borderRadius: 2,
                              backgroundColor: color,
                              border: "1px solid #E3D4C1"
                            }}
                          />
                        </Tooltip>
                      );
                    })}
                  </Fragment>
                ))}
              </Box>
            </Box>

            <Stack direction="row" spacing={1} alignItems="center" justifyContent="flex-end">
              <Typography variant="caption" color="text.secondary">
                Low
              </Typography>
              {COLOR_SCALE.map((color) => (
                <Box key={color} sx={{ width: 16, height: 12, borderRadius: 1, backgroundColor: color, border: "1px solid #E3D4C1" }} />
              ))}
              <Typography variant="caption" color="text.secondary">
                High
              </Typography>
            </Stack>
          </>
        )}
      </Box>
    </Paper>
  );
};

export default CalendarDailySpendHeatmap;
