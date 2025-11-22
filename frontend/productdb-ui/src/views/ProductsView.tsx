import { useMemo, useState } from "react";
import {
  Box,
  Drawer,
  Grid,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography
} from "@mui/material";
import PlaceholderCard from "../components/PlaceholderCard";
import { GlobalFilters } from "../components/GlobalFilterBar";

interface ProductRow {
  id: number;
  name: string;
  times: number;
  spend: number;
  lastPrice: number;
}

interface ProductsViewProps {
  filters: GlobalFilters;
}

const SAMPLE_PRODUCTS: ProductRow[] = [
  { id: 1, name: "Milk 1L", times: 34, spend: 12000, lastPrice: 135 },
  { id: 2, name: "Bread (whole grain)", times: 20, spend: 8000, lastPrice: 239 },
  { id: 3, name: "Eggs 10-pack", times: 12, spend: 6400, lastPrice: 320 },
  { id: 4, name: "Apples 1kg", times: 18, spend: 7100, lastPrice: 299 },
  { id: 5, name: "Oat Drink", times: 15, spend: 9300, lastPrice: 279 }
];

const ProductsView = ({ filters }: ProductsViewProps) => {
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(SAMPLE_PRODUCTS[0]?.id ?? null);
  const [drawerOpen, setDrawerOpen] = useState(false);

  const filteredProducts = useMemo(() => {
    if (!searchTerm) return SAMPLE_PRODUCTS;
    const lower = searchTerm.toLowerCase();
    return SAMPLE_PRODUCTS.filter((product) => product.name.toLowerCase().includes(lower));
  }, [searchTerm]);

  const selectedProduct = SAMPLE_PRODUCTS.find((p) => p.id === selectedId) ?? null;

  const detailPanel = selectedProduct ? (
    <Stack spacing={2} sx={{ p: 1 }}>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle2" color="text.secondary">
          Selected product
        </Typography>
        <Typography variant="h6" fontWeight={800}>
          {selectedProduct.name}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {selectedProduct.times} purchases • Last price: €{(selectedProduct.lastPrice / 100).toFixed(2)}
        </Typography>
      </Paper>
      <PlaceholderCard
        title="ProductPriceHistory"
        subtitle="Unit price over time (receipt_items)."
        height={180}
      />
      <PlaceholderCard
        title="ProductMerchantCompare"
        subtitle="Bar chart: price by merchant."
        height={160}
      />
      <PlaceholderCard
        title="ProductReceiptItemsList"
        subtitle="Latest items for the product."
        height={200}
      />
    </Stack>
  ) : (
    <Paper variant="outlined" sx={{ p: 2, height: "100%" }}>
      <Typography color="text.secondary">Select a product to view details.</Typography>
    </Paper>
  );

  return (
    <Stack spacing={2}>
      <Paper elevation={0} sx={{ p: 2.5, border: "1px solid #E3D4C1" }}>
        <Typography variant="h6" fontWeight={800} sx={{ mb: 0.5 }}>
          Products page
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Searchable product list on the left, price history and merchant comparison on the right.
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Filters: {filters.timeRange} • {filters.currency}
        </Typography>
      </Paper>

      <Grid container spacing={2} alignItems="stretch">
        <Grid item xs={12} md={6} lg={5}>
          <Paper elevation={0} sx={{ p: 2.5, height: "100%", border: "1px solid #E3D4C1" }}>
            <TextField
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              size="small"
              fullWidth
              placeholder="Search products"
              sx={{ mb: 2 }}
            />
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Name</TableCell>
                    <TableCell align="right">Times</TableCell>
                    <TableCell align="right">Spend</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {filteredProducts.map((product) => (
                    <TableRow
                      key={product.id}
                      hover
                      selected={product.id === selectedId}
                      onClick={() => {
                        setSelectedId(product.id);
                        setDrawerOpen(true);
                      }}
                      sx={{ cursor: "pointer" }}
                    >
                      <TableCell>
                        <Typography fontWeight={700}>{product.name}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Last price: €{(product.lastPrice / 100).toFixed(2)}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">{product.times}</TableCell>
                      <TableCell align="right">€{(product.spend / 100).toFixed(2)}</TableCell>
                    </TableRow>
                  ))}
                  {filteredProducts.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={3} align="center">
                        <Typography color="text.secondary">No products found.</Typography>
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </TableContainer>
          </Paper>
        </Grid>

        <Grid item xs={12} md={6} lg={7}>
          {detailPanel}
        </Grid>
      </Grid>

      <Drawer anchor="right" open={drawerOpen} onClose={() => setDrawerOpen(false)}>
        <Box sx={{ width: 360, p: 2 }}>{detailPanel}</Box>
      </Drawer>
    </Stack>
  );
};

export default ProductsView;
