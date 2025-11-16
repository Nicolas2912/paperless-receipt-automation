import {
  Avatar,
  Box,
  Chip,
  CircularProgress,
  FormControl,
  FormHelperText,
  Grid,
  InputLabel,
  MenuItem,
  Paper,
  Radio,
  RadioGroup,
  Select,
  Stack,
  Typography
} from "@mui/material";
import { SelectChangeEvent } from "@mui/material/Select";
import StoreIcon from "@mui/icons-material/Store";
import ReceiptIcon from "@mui/icons-material/Receipt";
import InventoryIcon from "@mui/icons-material/Inventory";
import TimelineIcon from "@mui/icons-material/Timeline";
import dayjs from "dayjs";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchMerchantSpend,
  fetchMerchants,
  fetchSpendTimeseries,
  fetchSummary,
  MerchantSpendItem,
  SpendTimeseriesPoint
} from "../api/client";
import { formatCurrency, formatDateTime, humaniseKey } from "../utils/format";

type RangeOption = {
  id: string;
  label: string;
  from: string | null;
  to: string | null;
};

const SpendTimelineChart = ({ points }: { points: SpendTimeseriesPoint[] }) => {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number } | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [canvasWidth, setCanvasWidth] = useState(900);
  const parsedPoints = useMemo(
    () =>
      points
        .map((point) => ({
          ...point,
          dateObj: dayjs(point.date)
        }))
        .filter((point) => point.dateObj.isValid())
        .sort((a, b) => a.dateObj.valueOf() - b.dateObj.valueOf()),
    [points]
  );

  useEffect(() => {
    if (!containerRef.current) {
      return;
    }
    const node = containerRef.current;
    const measure = () => {
      const nextWidth = node.getBoundingClientRect().width || canvasWidth;
      setCanvasWidth(Math.max(360, nextWidth));
    };
    measure();
    const observer = new ResizeObserver(() => measure());
    observer.observe(node);
    return () => observer.disconnect();
  }, [canvasWidth]);

  if (parsedPoints.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        No spend recorded in this period.
      </Typography>
    );
  }

  const minDate = parsedPoints[0].dateObj;
  const maxDate = parsedPoints[parsedPoints.length - 1].dateObj;
  const maxValue = Math.max(...parsedPoints.map((p) => p.total_gross_cents));
  const width = canvasWidth;
  const height = 320;
  const padLeft = 48;
  const padBottom = 40;
  const padTop = 16;
  const padRight = 8;

  const xSpanMs = Math.max(maxDate.diff(minDate, "millisecond"), 1);
  const ySpan = Math.max(maxValue, 1);

  const scaleX = (value: dayjs.Dayjs) =>
    padLeft + (value.diff(minDate, "millisecond") / xSpanMs) * (width - padLeft - padRight);
  const scaleY = (value: number) =>
    height - padBottom - (value / ySpan) * (height - padBottom - padTop);

  const areaPathD = parsedPoints
    .map((point, index) => {
      const x = scaleX(point.dateObj).toFixed(2);
      const y = scaleY(point.total_gross_cents).toFixed(2);
      return `${index === 0 ? "M" : "L"}${x} ${y}`;
    })
    .join(" ");

  const baselineY = scaleY(0);
  const gradientId = "spendGradient";

  const yTicks = Array.from({ length: 4 }, (_, i) => Math.round((maxValue / 4) * (i + 1)));
  const dateTicks = (() => {
    if (parsedPoints.length <= 3) {
      return parsedPoints.map((p) => p.dateObj);
    }
    const mid = parsedPoints[Math.floor(parsedPoints.length / 2)].dateObj;
    return [minDate, mid, maxDate];
  })();

  const handleMouseMove = (event: React.MouseEvent<SVGSVGElement, MouseEvent>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const distances = parsedPoints.map((p) => Math.abs(scaleX(p.dateObj) - x));
    const nearestIndex = distances.indexOf(Math.min(...distances));
    setHoverIndex(nearestIndex);

    const point = parsedPoints[nearestIndex];
    const pointX = scaleX(point.dateObj);
    const pointY = scaleY(point.total_gross_cents);
    // Convert SVG coords to rendered px
    const renderedX = (pointX / width) * rect.width;
    const renderedY = (pointY / height) * rect.height;
    const left = Math.min(Math.max(renderedX + 12, 8), rect.width - 180);
    const top = Math.min(Math.max(renderedY - 12, 8), rect.height - 90);
    setTooltipPos({ x: left, y: top });
  };

  const hoveredPoint = hoverIndex != null ? parsedPoints[hoverIndex] : null;

  return (
    <Box sx={{ width: "100%", position: "relative" }} ref={containerRef}>
      {hoveredPoint && (
        <Paper
          elevation={3}
          sx={{
            position: "absolute",
            top: tooltipPos?.y ?? 8,
            left: tooltipPos?.x ?? "auto",
            px: 1.5,
            py: 1,
            borderRadius: 2,
            background: "rgba(255,255,255,0.92)"
          }}
        >
          <Typography variant="caption" color="text.secondary">
            {hoveredPoint.dateObj.format("YYYY-MM-DD")}
          </Typography>
          <Typography variant="subtitle2" fontWeight={700}>
            {formatCurrency(hoveredPoint.total_gross_cents)}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {hoveredPoint.receipt_count} receipt{hoveredPoint.receipt_count === 1 ? "" : "s"}
          </Typography>
        </Paper>
      )}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", height: 280 }}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHoverIndex(null)}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#7c3aed" stopOpacity={0.18} />
            <stop offset="100%" stopColor="#7c3aed" stopOpacity={0.02} />
          </linearGradient>
        </defs>

        {/* Grid */}
        {yTicks.map((tick) => {
          const y = scaleY(tick);
          return (
            <g key={`y-${tick}`}>
              <line
                x1={padLeft}
                y1={y}
                x2={width - padRight}
                y2={y}
                stroke="#e5e7eb"
                strokeWidth={1}
                strokeDasharray="4 4"
              />
              <text x={10} y={y + 4} fontSize={11} fill="#6b7280">
                {formatCurrency(tick)}
              </text>
            </g>
          );
        })}

        {/* Axes */}
        <line
          x1={padLeft}
          y1={height - padBottom}
          x2={width - padRight}
          y2={height - padBottom}
          stroke="#cbd5e1"
          strokeWidth={1.2}
        />
        <line
          x1={padLeft}
          y1={padTop}
          x2={padLeft}
          y2={height - padBottom}
          stroke="#cbd5e1"
          strokeWidth={1.2}
        />

        {/* Area + line */}
        <path
          d={`${areaPathD} L ${scaleX(maxDate)} ${baselineY} L ${scaleX(minDate)} ${baselineY} Z`}
          fill={`url(#${gradientId})`}
          stroke="none"
        />
        <path d={areaPathD} fill="none" stroke="#7c3aed" strokeWidth={3} strokeLinejoin="round" />

        {/* Points */}
        {parsedPoints.map((point, idx) => {
          const x = scaleX(point.dateObj);
          const y = scaleY(point.total_gross_cents);
          const isHovered = idx === hoverIndex;
          return (
            <circle
              key={point.date}
              cx={x}
              cy={y}
              r={isHovered ? 6 : 4}
              fill={isHovered ? "#a855f7" : "#7c3aed"}
              stroke="#fff"
              strokeWidth={1.2}
            />
          );
        })}

        {/* X ticks */}
        {dateTicks.map((date, idx) => (
          <text
            key={`${date.toISOString()}-${idx}`}
            x={scaleX(date)}
            y={height - padBottom + 18}
            fontSize={11}
            fill="#6b7280"
            textAnchor={idx === 0 ? "start" : idx === dateTicks.length - 1 ? "end" : "middle"}
          >
            {date.format("MMM D")}
          </text>
        ))}
      </svg>
    </Box>
  );
};

const MerchantSpendChart = ({ items }: { items: MerchantSpendItem[] }) => {
  if (!items.length) {
    return (
      <Typography variant="body2" color="text.secondary">
        No merchant spend captured for this period.
      </Typography>
    );
  }
  const maxValue = Math.max(...items.map((item) => item.total_gross_cents));
  return (
    <Stack spacing={1.5}>
      {items.map((item) => {
        const widthPct = maxValue > 0 ? Math.max((item.total_gross_cents / maxValue) * 100, 4) : 0;
        return (
          <Stack key={item.merchant_id} spacing={0.5}>
            <Stack direction="row" alignItems="center" justifyContent="space-between">
              <Typography fontWeight={600}>{item.merchant_name}</Typography>
              <Typography variant="body2" color="text.secondary">
                {formatCurrency(item.total_gross_cents)}
              </Typography>
            </Stack>
            <Box
              sx={{
                height: 12,
                borderRadius: 999,
                backgroundColor: "#ede9fe",
                overflow: "hidden"
              }}
            >
              <Box
                sx={{
                  width: `${widthPct}%`,
                  height: "100%",
                  background: "linear-gradient(90deg, #7c3aed, #a855f7)"
                }}
              />
            </Box>
            <Typography variant="caption" color="text.secondary">
              {item.receipt_count} receipt{item.receipt_count === 1 ? "" : "s"}
            </Typography>
          </Stack>
        );
      })}
    </Stack>
  );
};

const DashboardView = () => {
  const monthOptions = useMemo<RangeOption[]>(() => {
    const now = dayjs();
    const options: RangeOption[] = [];
    for (let i = 0; i < 12; i += 1) {
      const start = now.subtract(i, "month").startOf("month");
      const end = start.endOf("month");
      options.push({
        id: start.format("YYYY-MM"),
        label: start.format("MMMM YYYY"),
        from: start.format("YYYY-MM-DD"),
        to: end.format("YYYY-MM-DD")
      });
    }
    options.push({ id: "all", label: "All time", from: null, to: null });
    return options;
  }, []);

  const [selectedRangeId, setSelectedRangeId] = useState<string>(() => {
    try {
      const stored = localStorage.getItem("productdb.range");
      if (stored) {
        return stored;
      }
    } catch (_) {
      /* ignore */
    }
    return "all";
  });
  const activeRange = monthOptions.find((opt) => opt.id === selectedRangeId) ?? monthOptions[0];
  const merchantRangeOptions = useMemo(() => {
    const now = dayjs();
    return [
      {
        id: "this-month",
        label: "This month",
        from: now.startOf("month").format("YYYY-MM-DD"),
        to: now.endOf("month").format("YYYY-MM-DD")
      },
      {
        id: "last-3-months",
        label: "Last 3 months",
        from: now.startOf("month").subtract(2, "month").format("YYYY-MM-DD"),
        to: now.endOf("month").format("YYYY-MM-DD")
      },
      {
        id: "this-year",
        label: "This year",
        from: now.startOf("year").format("YYYY-MM-DD"),
        to: now.endOf("year").format("YYYY-MM-DD")
      }
    ];
  }, []);
  const [merchantRangeId, setMerchantRangeId] = useState<string>(() => {
    try {
      const stored = localStorage.getItem("productdb.merchant-range");
      if (stored) {
        return stored;
      }
    } catch (_) {
      /* ignore */
    }
    return "this-month";
  });
  const activeMerchantRange =
    merchantRangeOptions.find((opt) => opt.id === merchantRangeId) ?? merchantRangeOptions[0];

  useEffect(() => {
    try {
      localStorage.setItem("productdb.range", selectedRangeId);
    } catch (_) {
      /* ignore */
    }
  }, [selectedRangeId]);

  useEffect(() => {
    try {
      localStorage.setItem("productdb.merchant-range", merchantRangeId);
    } catch (_) {
      /* ignore */
    }
  }, [merchantRangeId]);

  const summaryQuery = useQuery({
    queryKey: ["summary", activeRange?.from, activeRange?.to],
    queryFn: () =>
      fetchSummary({
        dateFrom: activeRange?.from ?? undefined,
        dateTo: activeRange?.to ?? undefined
      }),
    placeholderData: (previousData) => previousData,
    enabled: Boolean(activeRange)
  });
  const spendQuery = useQuery({
    queryKey: ["spend-timeseries", activeRange?.from, activeRange?.to],
    queryFn: () =>
      fetchSpendTimeseries({
        dateFrom: activeRange?.from ?? undefined,
        dateTo: activeRange?.to ?? undefined
      }),
    placeholderData: (previousData) => previousData,
    enabled: Boolean(activeRange)
  });
  const merchantSpendQuery = useQuery({
    queryKey: ["merchant-spend", activeMerchantRange?.from, activeMerchantRange?.to],
    queryFn: () =>
      fetchMerchantSpend(
        {
          dateFrom: activeMerchantRange?.from ?? undefined,
          dateTo: activeMerchantRange?.to ?? undefined
        },
        8
      ),
    placeholderData: (previousData) => previousData,
    enabled: Boolean(activeMerchantRange)
  });
  const merchantsQuery = useQuery({ queryKey: ["merchants"], queryFn: fetchMerchants });

  const handleRangeChange = (event: SelectChangeEvent<string>) => {
    setSelectedRangeId(event.target.value);
  };

  if (
    summaryQuery.isError ||
    spendQuery.isError ||
    merchantSpendQuery.isError ||
    !summaryQuery.data ||
    !spendQuery.data ||
    !merchantSpendQuery.data
  ) {
    return <Typography color="error">Unable to load dashboard data.</Typography>;
  }

  if (!summaryQuery.data || !spendQuery.data || !merchantSpendQuery.data) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  const summary = summaryQuery.data;
  const merchants = merchantsQuery.data?.items ?? [];

  const rangeSummary =
    summary.range ??
    ({
      filters: { date_from: null, date_to: null },
      counts: {
        receipts: summary.counts.receipts ?? 0,
        receipt_items: summary.counts.receipt_items ?? 0,
        merchants: summary.counts.merchants ?? 0,
        addresses: summary.counts.addresses ?? 0
      },
      totals: summary.totals,
      timespan: summary.timespan,
      daily_totals: []
    } satisfies typeof summary.range);

  const totalGross = rangeSummary.totals.total_gross_cents;
  const totalNet = rangeSummary.totals.total_net_cents;
  const totalTax = rangeSummary.totals.total_tax_cents;

  const topMerchants = merchants
    .filter((m) => m.total_gross_cents > 0)
    .sort((a, b) => b.total_gross_cents - a.total_gross_cents)
    .slice(0, 5);

  const rangeDescription =
    rangeSummary.filters.date_from && rangeSummary.filters.date_to
      ? `${dayjs(rangeSummary.filters.date_from).format("MMM D, YYYY")} – ${dayjs(
          rangeSummary.filters.date_to
        ).format("MMM D, YYYY")}`
      : "All time";

  return (
    <Stack spacing={4}>
      <Stack
        direction={{ xs: "column", sm: "row" }}
        spacing={2}
        alignItems={{ xs: "flex-start", sm: "center" }}
        justifyContent="space-between"
      >
        <Box>
          <Typography variant="h5">Overview</Typography>
          <Typography variant="body2" color="text.secondary">
            Showing {rangeDescription}
          </Typography>
        </Box>
        <FormControl size="small" sx={{ minWidth: 220 }}>
          <InputLabel id="range-select-label">Period</InputLabel>
          <Select
            labelId="range-select-label"
            label="Period"
            value={selectedRangeId}
            onChange={handleRangeChange}
          >
            {monthOptions.map((option) => (
              <MenuItem key={option.id} value={option.id}>
                {option.label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>

      <Grid container spacing={3}>
        <Grid item xs={12} md={4}>
          <Paper elevation={0} sx={{ p: 3, height: "100%" }}>
            <Stack direction="row" spacing={2} alignItems="center">
              <Avatar sx={{ bgcolor: "primary.main" }}>
                <ReceiptIcon />
              </Avatar>
              <Box>
                <Typography variant="h6">Receipts</Typography>
                <Typography variant="h4">{rangeSummary.counts.receipts ?? 0}</Typography>
                <Typography variant="body2" color="text.secondary">
                  {rangeSummary.counts.receipt_items ?? 0} line items captured
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
                <Typography variant="h4">{rangeSummary.counts.merchants ?? 0}</Typography>
                <Typography variant="body2" color="text.secondary">
                  {rangeSummary.counts.addresses ?? 0} locations in this period
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
                  Net {formatCurrency(totalNet)} · Tax {formatCurrency(totalTax)}
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
                First purchase: <strong>{formatDateTime(rangeSummary.timespan.first_purchase)}</strong>
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Latest purchase: <strong>{formatDateTime(rangeSummary.timespan.last_purchase)}</strong>
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Filtered period: {rangeDescription}
              </Typography>
            </Stack>
          </Paper>
        </Grid>
        <Grid item xs={12} md={6}>
          <Paper elevation={0} sx={{ p: 3, height: "100%" }}>
            <Typography variant="h6" gutterBottom>
              Top Merchants (all-time gross spend)
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
        <Stack spacing={1}>
          <Typography variant="h6">Spend Timeline</Typography>
          <SpendTimelineChart points={spendQuery.data.points} />
        </Stack>
      </Paper>

      <Paper elevation={0} sx={{ p: 3 }}>
        <Stack spacing={2}>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1} justifyContent="space-between">
            <Box>
              <Typography variant="h6">Spending by Merchant (top 8)</Typography>
              <FormHelperText>Who gets most of my money?</FormHelperText>
            </Box>
            <RadioGroup
              row
              value={merchantRangeId}
              onChange={(event) => setMerchantRangeId(event.target.value)}
              sx={{ flexWrap: "wrap", gap: 1 }}
            >
              {merchantRangeOptions.map((opt) => (
                <FormControl key={opt.id} component="fieldset" sx={{ mr: 1 }}>
                  <Stack direction="row" alignItems="center">
                    <Radio value={opt.id} size="small" />
                    <FormHelperText sx={{ m: 0, lineHeight: 1.4 }}>{opt.label}</FormHelperText>
                  </Stack>
                </FormControl>
              ))}
            </RadioGroup>
          </Stack>
          <MerchantSpendChart items={merchantSpendQuery.data.items} />
        </Stack>
      </Paper>

      <Paper elevation={0} sx={{ p: 3 }}>
        <Typography variant="h6" gutterBottom>
          Table Counts (all time)
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
