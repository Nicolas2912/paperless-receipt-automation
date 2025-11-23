import { useMemo, useState } from "react";
import {
  Box,
  Divider,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Paper,
  Stack,
  Typography
} from "@mui/material";
import SpaceDashboardIcon from "@mui/icons-material/SpaceDashboard";
import ReceiptLongIcon from "@mui/icons-material/ReceiptLong";
import StorefrontIcon from "@mui/icons-material/Storefront";
import InsightsIcon from "@mui/icons-material/Insights";
import InventoryIcon from "@mui/icons-material/Inventory";
import VerifiedUserIcon from "@mui/icons-material/VerifiedUser";
import SettingsIcon from "@mui/icons-material/Settings";
import { useQuery } from "@tanstack/react-query";
import GlobalFilterBar, { GlobalFilters } from "./components/GlobalFilterBar";
import DashboardView from "./views/DashboardView";
import ReceiptsView from "./views/ReceiptsView";
import MerchantsView from "./views/MerchantsView";
import ProductsView from "./views/ProductsView";
import InsightsView from "./views/InsightsView";
import DataQualityView from "./views/DataQualityView";
import AIChatView from "./views/AIChatView";
import { fetchMerchants, fetchSummary } from "./api/client";
import { formatCurrency } from "./utils/format";

type PageKey = "dashboard" | "merchants" | "products" | "insights" | "receipts" | "data-quality" | "ai-chat";

const navItems: Array<{ key: PageKey; label: string; icon: React.ReactNode; description: string }> = [
  { key: "dashboard", label: "Dashboard", icon: <SpaceDashboardIcon fontSize="small" />, description: "At a glance" },
  { key: "merchants", label: "Merchants", icon: <StorefrontIcon fontSize="small" />, description: "Master–detail" },
  { key: "products", label: "Products", icon: <InventoryIcon fontSize="small" />, description: "Price tracking" },
  { key: "insights", label: "Insights", icon: <InsightsIcon fontSize="small" />, description: "Behaviour" },
  { key: "receipts", label: "Receipts", icon: <ReceiptLongIcon fontSize="small" />, description: "Drill-down" },
  { key: "ai-chat", label: "AI Chat", icon: <InsightsIcon fontSize="small" />, description: "Ask about your data" },
  { key: "data-quality", label: "Data Quality", icon: <VerifiedUserIcon fontSize="small" />, description: "Runs & QC" }
];

const UI_COLORS = {
  background: "#809848",
  backgroundGradient: "radial-gradient(circle at 20% 20%, #8aa352 0%, #71883e 40%, #5f7235 100%)",
  backgroundHighlight: "radial-gradient(600px at 70% 15%, rgba(255,255,255,0.10), rgba(255,255,255,0))",
  backgroundVignette: "radial-gradient(120% 120% at 50% 50%, rgba(0,0,0,0) 60%, rgba(0,0,0,0.08) 100%)",
  surface: "#FFF8EE",
  sidebarGradient: "linear-gradient(160deg, #f5ffef 0%, #bee7b8 52%, #a8d5a3 100%)",
  border: "#E3D4C1",
  subtle: "#F6E6D4",
  accentSoft: "#FDC4BE",
  accentHover: "#F6E6D4"
};

const App = () => {
  const [activePage, setActivePage] = useState<PageKey>("dashboard");
  const [filters, setFilters] = useState<GlobalFilters>({
    timeRange: "this_month",
    currency: "EUR",
    merchantId: null,
    search: ""
  });

  const merchantsQuery = useQuery({ queryKey: ["merchants"], queryFn: fetchMerchants });
  const summaryQuery = useQuery({ queryKey: ["summary"], queryFn: () => fetchSummary() });

  const quickStats = useMemo(() => {
    const counts = summaryQuery.data?.counts ?? {};
    const totals = summaryQuery.data?.totals;
    return [
      { label: "Receipts", value: counts.receipts ?? "—" },
      { label: "Products", value: counts.receipt_items ?? "—" },
      { label: "Merchants", value: counts.merchants ?? "—" },
      {
        label: "Total Gross",
        value: totals ? formatCurrency(totals.total_gross_cents) : "—"
      }
    ];
  }, [summaryQuery.data]);

  const renderPage = () => {
    switch (activePage) {
      case "dashboard":
        return <DashboardView filters={filters} />;
      case "merchants":
        return <MerchantsView filters={filters} />;
      case "products":
        return <ProductsView filters={filters} />;
      case "insights":
        return <InsightsView filters={filters} />;
      case "receipts":
        return <ReceiptsView filters={filters} />;
      case "data-quality":
        return <DataQualityView filters={filters} />;
      case "ai-chat":
        return <AIChatView filters={filters} />;
      default:
        return null;
    }
  };

  return (
    <Box
      sx={{
        minHeight: "100vh",
        backgroundImage: `${UI_COLORS.backgroundHighlight}, ${UI_COLORS.backgroundVignette}, ${UI_COLORS.backgroundGradient}`,
        backgroundRepeat: "no-repeat",
        display: "flex",
        gap: 3,
        p: { xs: 2, md: 3 }
      }}
    >
      <Paper
        elevation={0}
        sx={{
          width: { xs: 280, md: 280 },
          flexShrink: 0,
          border: `1px solid ${UI_COLORS.border}`,
          background: UI_COLORS.sidebarGradient,
          display: "flex",
          flexDirection: "column",
          gap: 2,
          p: 2,
          height: "fit-content",
          position: "sticky",
          top: 16
        }}
      >
        <Stack spacing={0.5}>
          <Typography variant="overline" color="text.secondary">
            Analytics suite
          </Typography>
          <Typography variant="h6" fontWeight={800}>
            Receipt Analyzer
          </Typography>
        </Stack>

        <List dense>
          {navItems.map((item) => (
            <ListItemButton
              key={item.key}
              selected={item.key === activePage}
              onClick={() => setActivePage(item.key)}
              sx={{
                borderRadius: 2,
                mb: 0.5,
                border: item.key === activePage ? `1px solid #BC6C25` : `1px solid ${UI_COLORS.border}`,
                background:
                  item.key === activePage
                    ? "linear-gradient(90deg, #FFF8EE 0%, #F6E6D4 100%)"
                    : UI_COLORS.surface,
                "&:hover": {
                  background: "linear-gradient(90deg, #FFF8EE 0%, #F6E6D4 100%)"
                },
                boxShadow: item.key === activePage ? "inset 4px 0 0 #BC6C25, 0 6px 18px rgba(40,54,24,0.12)" : "none"
              }}
            >
              <ListItemIcon sx={{ minWidth: 40 }}>
                <Box
                  sx={{
                    width: 30,
                    height: 30,
                    borderRadius: 2,
                    display: "grid",
                    placeItems: "center",
                    backgroundColor: item.key === activePage ? UI_COLORS.accentSoft : UI_COLORS.surface,
                    border: `1px solid ${UI_COLORS.border}`,
                    boxShadow: item.key === activePage ? "0 0 0 1px #BC6C25 inset" : "none"
                  }}
                >
                  {item.icon}
                </Box>
              </ListItemIcon>
              <ListItemText
                primary={<Typography fontWeight={700}>{item.label}</Typography>}
                secondary={<Typography variant="caption">{item.description}</Typography>}
              />
            </ListItemButton>
          ))}
        </List>

        <Divider />

        <Stack spacing={1.5}>
          <Typography variant="subtitle2" color="text.secondary">
            Quick stats
          </Typography>
          {quickStats.map((stat) => (
            <Paper
              key={stat.label}
              elevation={0}
              sx={{ p: 1.5, border: `1px dashed ${UI_COLORS.border}`, backgroundColor: UI_COLORS.surface }}
            >
              <Typography variant="caption" color="text.secondary">
                {stat.label}
              </Typography>
              <Typography variant="subtitle1" fontWeight={700}>
                {stat.value}
              </Typography>
            </Paper>
          ))}
        </Stack>
      </Paper>

      <Box sx={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
        <Paper
          elevation={0}
          sx={{
            border: `1px solid ${UI_COLORS.border}`,
            p: { xs: 2, md: 2.5 },
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 2,
            background: "#819D75"
          }}
        >
          <Box>
            <Typography variant="h5" fontWeight={800}>
              Receipt Analyzer
            </Typography>
          </Box>
          <IconButton aria-label="settings">
            <SettingsIcon />
          </IconButton>
        </Paper>

        <GlobalFilterBar
          filters={filters}
          onChange={(updates) => setFilters((prev) => ({ ...prev, ...updates }))}
          merchants={merchantsQuery.data?.items ?? []}
          merchantsLoading={merchantsQuery.isLoading}
        />

        {renderPage()}

        <Paper
          elevation={0}
          sx={{
            mt: 1,
            border: `1px solid ${UI_COLORS.border}`,
            p: 1.5,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            background: UI_COLORS.surface
          }}
        >
          <Typography variant="body2" color="text.secondary">
            Status: {summaryQuery.data?.counts?.receipts ?? "—"} receipts • Last sync:{" "}
            {summaryQuery.data?.timespan?.last_purchase ?? "—"}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Filters apply to all views
          </Typography>
        </Paper>
      </Box>
    </Box>
  );
};

export default App;
