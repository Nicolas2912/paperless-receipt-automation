import { useState } from "react";
import {
  AppBar,
  Box,
  Container,
  Tab,
  Tabs,
  Toolbar,
  Typography
} from "@mui/material";
import SpaceDashboardIcon from "@mui/icons-material/SpaceDashboard";
import ReceiptLongIcon from "@mui/icons-material/ReceiptLong";
import StorefrontIcon from "@mui/icons-material/Storefront";
import TableChartIcon from "@mui/icons-material/TableChart";
import DashboardView from "./views/DashboardView";
import ReceiptsView from "./views/ReceiptsView";
import MerchantsView from "./views/MerchantsView";
import TablesView from "./views/TablesView";

const tabs = [
  { value: "dashboard", label: "Overview", icon: <SpaceDashboardIcon fontSize="small" /> },
  { value: "receipts", label: "Receipts", icon: <ReceiptLongIcon fontSize="small" /> },
  { value: "merchants", label: "Merchants", icon: <StorefrontIcon fontSize="small" /> },
  { value: "tables", label: "Tables", icon: <TableChartIcon fontSize="small" /> }
];

const App = () => {
  const [activeTab, setActiveTab] = useState<string>("dashboard");

  return (
    <Box sx={{ minHeight: "100vh", background: "linear-gradient(180deg, #f5f7fa 0%, #eef2f7 100%)" }}>
      <AppBar position="sticky" elevation={0} sx={{ backgroundColor: "#ffffff", color: "#0f172a", borderBottom: "1px solid #e2e8f0" }}>
        <Toolbar sx={{ display: "flex", flexDirection: { xs: "column", sm: "row" }, alignItems: { xs: "flex-start", sm: "center" }, gap: 1 }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            ProductDB Dashboard
          </Typography>
          <Tabs
            value={activeTab}
            onChange={(_, value) => setActiveTab(value)}
            textColor="inherit"
            indicatorColor="secondary"
            variant="scrollable"
            scrollButtons="auto"
            sx={{ ml: { sm: 4 }, mt: { xs: 1, sm: 0 } }}
          >
            {tabs.map((tab) => (
              <Tab key={tab.value} value={tab.value} icon={tab.icon} iconPosition="start" label={tab.label} />
            ))}
          </Tabs>
        </Toolbar>
      </AppBar>

      <Container maxWidth="xl" sx={{ py: 4 }}>
        {activeTab === "dashboard" && <DashboardView />}
        {activeTab === "receipts" && <ReceiptsView />}
        {activeTab === "merchants" && <MerchantsView />}
        {activeTab === "tables" && <TablesView />}
      </Container>
    </Box>
  );
};

export default App;
