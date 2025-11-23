import { useMemo, useState } from "react";
import { Box, Chip, CircularProgress, Paper, Stack, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import axios from "axios";
import { fetchMonthlySpend, fetchSpendTimeseries, MonthlySpendPoint, SpendTimeseriesPoint } from "../api/client";
import { GlobalFilters } from "./GlobalFilterBar";
import { formatCurrency } from "../utils/format";
import { resolveDateRange } from "../utils/dateRange";

interface MonthlySpendLineChartProps {
  filters: GlobalFilters;
  height?: number;
}

type ChartPoint = {
  key: string;
  value: number;
  count: number;
  date: dayjs.Dayjs;
  label: string;
  shortLabel: string;
};

const buildPaths = (points: ChartPoint[]) => {
  if (points.length === 0) {
    return {
      pathD: "",
      coords: [],
      labels: [] as Array<{ x: number; label: string }>,
      yTicks: [] as Array<{ y: number; value: number }>,
      viewBox: { width: 480, height: 140 },
      margin: { top: 4, right: 14, bottom: 22, left: 12 }
    };
  }

  const maxValueRaw = Math.max(...points.map((p) => p.value), 0);
  const maxValue = maxValueRaw <= 0 ? 1 : maxValueRaw * 1.05; // add headroom
  const viewWidth = 480;
  const viewHeight = 148;
  const margin = { top: 4, right: 14, bottom: 22, left: 12 };
  const usableWidth = viewWidth - margin.left - margin.right;
  const usableHeight = viewHeight - margin.top - margin.bottom;
  const denom = Math.max(points.length - 1, 1);

  const coords = points.map((point, index) => {
    const x = margin.left + (index / denom) * usableWidth;
    const ratio = maxValue === 0 ? 0 : point.value / maxValue;
    const y = margin.top + (1 - ratio) * usableHeight;
    return { x, y };
  });

  // When only one data point exists, draw a short horizontal stroke so the series is visible.
  let renderCoords = coords;
  if (points.length === 1) {
    const y = coords[0].y;
    renderCoords = [
      { x: margin.left, y },
      { x: viewWidth - margin.right, y }
    ];
  }

  const pathD = renderCoords
    .map((coord, index) => `${index === 0 ? "M" : "L"} ${coord.x.toFixed(2)} ${coord.y.toFixed(2)}`)
    .join(" ");

  const labelStep = Math.max(1, Math.ceil(points.length / 6));
  const labels = points
    .map((point, index) => {
      if (index % labelStep !== 0 && index !== points.length - 1) {
        return null;
      }
      return { x: coords[index].x, label: point.shortLabel };
    })
    .filter(Boolean) as Array<{ x: number; label: string }>;

  const yTicks = Array.from({ length: 5 }, (_, idx) => {
    const ratio = idx / 4;
    const value = maxValue * (1 - ratio);
    const y = margin.top + ratio * usableHeight;
    return { y, value };
  });

  return {
    pathD,
    coords,
    labels,
    yTicks,
    viewBox: { width: viewWidth, height: viewHeight },
    margin
  };
};

const aggregateTimeseriesToMonthly = (points: SpendTimeseriesPoint[]): MonthlySpendPoint[] => {
  const bucket: Record<string, { gross: number; count: number }> = {};
  points.forEach((point) => {
    const monthKey = dayjs(point.date).format("YYYY-MM");
    if (!bucket[monthKey]) {
      bucket[monthKey] = { gross: 0, count: 0 };
    }
    bucket[monthKey].gross += point.total_gross_cents ?? 0;
    bucket[monthKey].count += point.receipt_count ?? 0;
  });
  return Object.entries(bucket)
    .map(([month, totals]) => ({
      month,
      total_gross_cents: totals.gross,
      receipt_count: totals.count
    }))
    .sort((a, b) => (a.month < b.month ? -1 : a.month > b.month ? 1 : 0));
};

const MonthlySpendLineChart = ({ filters, height = 420 }: MonthlySpendLineChartProps) => {
  const [interval, setInterval] = useState<"monthly" | "daily">("monthly");
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const range = resolveDateRange(filters.timeRange);
  const monthlySpendQuery = useQuery({
    queryKey: ["monthly-spend", range.from, range.to],
    queryFn: async () => {
      try {
        return await fetchMonthlySpend({ dateFrom: range.from, dateTo: range.to });
      } catch (error) {
        const status = axios.isAxiosError(error) ? error.response?.status : null;
        // Fall back to daily timeseries for backwards compatibility (older API versions)
        if (status && status !== 404) {
          throw error;
        }
        const timeseries = await fetchSpendTimeseries({ dateFrom: range.from, dateTo: range.to });
        return { filters: timeseries.filters, points: aggregateTimeseriesToMonthly(timeseries.points) };
      }
    }
  });
  const dailySpendQuery = useQuery({
    queryKey: ["daily-spend", range.from, range.to],
    queryFn: () => fetchSpendTimeseries({ dateFrom: range.from, dateTo: range.to })
  });

  const timeLabel = useMemo(() => {
    switch (filters.timeRange) {
      case "this_month":
        return "This Month";
      case "this_year":
        return "This Year";
      case "last_12_months":
        return "Last 12 Months";
      default:
        return filters.timeRange || "All time";
    }
  }, [filters.timeRange]);

  const chartHeight = Math.max(height - 170, 280);

  const monthlyPoints = useMemo<ChartPoint[]>(() => {
    const pts = monthlySpendQuery.data?.points ?? [];
    return pts.map((p) => {
      const date = dayjs(p.month + "-01");
      return {
        key: p.month,
        value: p.total_gross_cents ?? 0,
        count: p.receipt_count ?? 0,
        date,
        label: date.format("MMMM YYYY"),
        shortLabel: date.format("MMM YY")
      };
    });
  }, [monthlySpendQuery.data?.points]);

  const dailyPoints = useMemo<ChartPoint[]>(() => {
    const pts = dailySpendQuery.data?.points ?? [];
    return pts.map((p) => {
      const date = dayjs(p.date);
      return {
        key: p.date,
        value: p.total_gross_cents ?? 0,
        count: p.receipt_count ?? 0,
        date,
        label: date.format("DD MMM YYYY"),
        shortLabel: date.format("DD MMM")
      };
    });
  }, [dailySpendQuery.data?.points]);

  const activePoints = interval === "monthly" ? monthlyPoints : dailyPoints;
  const totals = useMemo(() => {
    const grossSum = activePoints.reduce((sum, point) => sum + (point.value ?? 0), 0);
    const receiptCount = activePoints.reduce((sum, point) => sum + (point.count ?? 0), 0);
    const first = activePoints[0]?.date;
    const last = activePoints[activePoints.length - 1]?.date;
    const formattedRange =
      first && last ? `${first.format("MMM YYYY")} – ${last.format("MMM YYYY")}` : "No data";
    const averageValue = activePoints.length > 0 ? grossSum / activePoints.length : 0;
    return { grossSum, receiptCount, formattedRange, averageValue };
  }, [activePoints]);

  const { pathD, coords, labels, yTicks, viewBox, margin } = useMemo(
    () => buildPaths(activePoints),
    [activePoints]
  );
  const lastPoint = activePoints[activePoints.length - 1];
  const lastCoord = coords[coords.length - 1];
  const firstPoint = activePoints[0];
  const firstCoord = coords[0];
  const formatPointLabel = (point: ChartPoint) =>
    interval === "monthly" ? point.date.format("MMM YYYY") : point.date.format("DD MMM YYYY");
  const isLoading = interval === "monthly" ? monthlySpendQuery.isLoading : dailySpendQuery.isLoading;
  const isError = interval === "monthly" ? monthlySpendQuery.isError : dailySpendQuery.isError;

  const formatTickValue = (value: number) =>
    new Intl.NumberFormat(undefined, {
      minimumFractionDigits: 0,
      maximumFractionDigits: 0
    }).format((value ?? 0) / 100);
  const averageUnitLabel = interval === "monthly" ? "month" : "day";
  const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

  const tooltipMeta =
    hoverIndex !== null && activePoints[hoverIndex] && coords[hoverIndex]
      ? (() => {
          const rawLeft = (coords[hoverIndex].x / viewBox.width) * 100;
          const rawTop = (coords[hoverIndex].y / viewBox.height) * 100;
          const left = clamp(rawLeft, 6, 94);
          const top = clamp(rawTop, 10, 90);
          const translateY = top < 18 ? "12%" : top > 78 ? "-80%" : "-110%";
          return { left, top, translateY };
        })()
      : null;

  return (
    <Paper
      elevation={0}
      sx={{
        p: 2.5,
        pr: 2.5,
        height,
        border: "1px solid #E3D4C1",
        position: "relative",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column"
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="baseline" spacing={2}>
        <Stack spacing={0.5}>
          <Typography variant="h6" fontWeight={800}>
            Monthly spend
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Sum of total_gross per month. Filters apply to the underlying receipts; currency shown as {filters.currency}.
          </Typography>
          <Stack spacing={0.25}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Chip size="small" label={timeLabel} />
              <Typography variant="caption" color="text.secondary">
                Range: {totals.formattedRange}
              </Typography>
            </Stack>
            <Stack spacing={0.25}>
              <Stack direction="row" spacing={1} alignItems="center">
                <Chip size="small" label="Average" />
                <Typography variant="subtitle2" fontWeight={700}>
                  {formatCurrency(totals.averageValue, filters.currency)}
                </Typography>
              </Stack>
              <Typography variant="caption" color="text.secondary">
                Per {averageUnitLabel}
              </Typography>
            </Stack>
          </Stack>
        </Stack>
        <Stack alignItems="flex-end" spacing={0.75}>
          <ToggleButtonGroup
            size="small"
            value={interval}
            exclusive
            onChange={(_, value) => value && setInterval(value)}
          >
            <ToggleButton value="monthly">Monthly</ToggleButton>
            <ToggleButton value="daily">Daily</ToggleButton>
          </ToggleButtonGroup>
          <Stack alignItems="flex-end" spacing={0.25}>
            <Typography variant="caption" color="text.secondary">
              Total gross (range)
            </Typography>
            <Typography variant="h6" fontWeight={800}>
              {formatCurrency(totals.grossSum, filters.currency)}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {totals.receiptCount} receipts
            </Typography>
          </Stack>
          </Stack>
        </Stack>

      <Box sx={{ mt: 1, position: "relative", flex: 1, minHeight: chartHeight, overflow: "hidden" }}>
        {isLoading ? (
          <Stack height="100%" alignItems="center" justifyContent="center">
            <CircularProgress size={28} />
          </Stack>
        ) : isError ? (
          <Stack height="100%" alignItems="center" justifyContent="center" spacing={1}>
            <Typography variant="subtitle1" fontWeight={700}>
              Could not load spend data
            </Typography>
            <Typography variant="body2" color="text.secondary" textAlign="center">
              {axios.isAxiosError(
                interval === "monthly" ? monthlySpendQuery.error : dailySpendQuery.error
              )
                ? (interval === "monthly" ? monthlySpendQuery.error?.message : dailySpendQuery.error?.message)
                : "Unexpected error"}
            </Typography>
          </Stack>
        ) : activePoints.length === 0 ? (
          <Stack height="100%" alignItems="center" justifyContent="center" spacing={1}>
            <Typography variant="subtitle1" fontWeight={700}>
              No monthly spend data yet
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Import receipts to see trends over time.
            </Typography>
          </Stack>
        ) : (
          <Box sx={{ height: "100%", position: "relative", px: 0, overflow: "hidden" }}>
            <svg
              width="100%"
              height="100%"
              viewBox={`0 0 ${viewBox.width} ${viewBox.height}`}
              preserveAspectRatio="xMidYMid meet"
              shapeRendering="geometricPrecision"
            >
              {/* Y grid + axis */}
              {yTicks.map((tick, idx) => (
                <g key={tick.y}>
                  <line
                    x1={margin.left}
                    x2={viewBox.width - margin.right}
                    y1={tick.y}
                    y2={tick.y}
                    stroke="#E3D4C1"
                    strokeWidth={idx === yTicks.length - 1 ? 1.35 : 0.95}
                  strokeDasharray={idx === yTicks.length - 1 ? "4 2" : "2.4 2.4"}
                  vectorEffect="non-scaling-stroke"
                />
                <text x={4} y={tick.y + 3} fontSize={6.6} fill="#4a4036" fontWeight={700}>
                  {formatTickValue(tick.value)}
                </text>
              </g>
            ))}
              {/* X axis */}
              <line
                x1={margin.left}
                x2={viewBox.width - margin.right}
                y1={viewBox.height - margin.bottom}
                y2={viewBox.height - margin.bottom}
                stroke="#C9BBA8"
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
              />
              {/* Y axis */}
              <line
                x1={margin.left}
                x2={margin.left}
                y1={margin.top}
                y2={viewBox.height - margin.bottom}
                stroke="#C9BBA8"
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
              />
              {labels.map((label) => (
                <g key={`grid-${label.label}-${label.x}`}>
                  <line
                    x1={label.x}
                    x2={label.x}
                    y1={margin.top}
                    y2={viewBox.height - margin.bottom}
                    stroke="#EADBC9"
                    strokeWidth={0.7}
                    strokeDasharray="1.8 2.2"
                    vectorEffect="non-scaling-stroke"
                  />
                </g>
              ))}
              {labels.map((label) => (
                <text
                  key={label.label + label.x}
                  x={label.x}
                  y={viewBox.height - margin.bottom + 6}
                  fontSize={7.3}
                  fill="#4a4036"
                  fontWeight={700}
                  textAnchor="middle"
                  alignmentBaseline="hanging"
                >
                  {label.label}
                </text>
              ))}
              {labels.map((label) => (
                <line
                  key={`tick-${label.label}-${label.x}`}
                  x1={label.x}
                  x2={label.x}
                  y1={viewBox.height - margin.bottom + 2}
                  y2={viewBox.height - margin.bottom - 2}
                  stroke="#6b5b4d"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
              <path
                d={pathD}
                fill="none"
                stroke="#BC6C25"
                strokeWidth={2.4}
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
              />
              {coords.map((coord, index) => {
                const isHovered = hoverIndex === index;
                return (
                  <circle
                    key={activePoints[index].key}
                    cx={coord.x}
                    cy={coord.y}
                    r={isHovered ? 3.6 : activePoints.length <= 2 ? 2.4 : 2.2}
                    fill={isHovered ? "#8C4A14" : index === coords.length - 1 ? "#BC6C25" : "#D6C4B2"}
                    stroke="#FFF8EE"
                    strokeWidth={0.9}
                    vectorEffect="non-scaling-stroke"
                    onMouseEnter={() => setHoverIndex(index)}
                    onMouseLeave={() => setHoverIndex(null)}
                  />
                );
              })}
            </svg>
            {tooltipMeta && hoverIndex !== null && (
              <Paper
                elevation={0}
                sx={{
                  position: "absolute",
                  left: `${tooltipMeta.left}%`,
                  top: `${tooltipMeta.top}%`,
                  transform: `translate(-50%, ${tooltipMeta.translateY})`,
                  p: 0.9,
                  border: "1px solid #E3D4C1",
                  backgroundColor: "rgba(255, 248, 238, 0.95)",
                  boxShadow: "0 8px 24px rgba(0,0,0,0.08)",
                  pointerEvents: "none",
                  maxWidth: "60%"
                }}
              >
                <Typography variant="caption" color="text.secondary">
                  {formatPointLabel(activePoints[hoverIndex])}
                </Typography>
                <Typography variant="subtitle2" fontWeight={800}>
                  {formatCurrency(activePoints[hoverIndex].value, filters.currency)}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {activePoints[hoverIndex].count} receipts
                </Typography>
              </Paper>
            )}
          </Box>
        )}
      </Box>
    </Paper>
  );
};

export default MonthlySpendLineChart;
