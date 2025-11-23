import { createTheme } from "@mui/material/styles";

const theme = createTheme({
  palette: {
    mode: "light",
    primary: {
      main: "#283618"
    },
    secondary: {
      main: "#BC6C25"
    },
    success: {
      main: "#809848"
    },
    background: {
      default: "#809848",
      paper: "#FFF8EE"
    },
    text: {
      primary: "#1f2615",
      secondary: "#4a5137"
    },
    divider: "#e3d4c1"
  },
  typography: {
    fontFamily: "'IBM Plex Sans', 'DM Sans', 'Segoe UI', 'Arial', sans-serif",
    allVariants: {
      fontVariantNumeric: "tabular-nums",
      fontFeatureSettings: '"tnum" 1, "lnum" 1'
    },
    h4: {
      fontWeight: 600,
      letterSpacing: "-0.01em"
    },
    h5: {
      fontWeight: 600,
      letterSpacing: "-0.01em"
    },
    button: {
      fontWeight: 600,
      letterSpacing: "0.01em"
    }
  },
  shape: {
    borderRadius: 12
  },
  components: {
    MuiButton: {
      styleOverrides: {
        contained: {
          boxShadow: "none"
        }
      }
    },
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          fontVariantNumeric: "tabular-nums",
          fontFeatureSettings: '"tnum" 1, "lnum" 1'
        },
        ".numeric, .numeric-text, .MuiTableCell-alignRight": {
          fontFamily:
            "'IBM Plex Mono', 'DM Mono', 'SFMono-Regular', 'ui-monospace', 'Consolas', 'Liberation Mono', 'Menlo', 'Courier New', monospace",
          letterSpacing: "0.01em",
          fontVariantNumeric: "tabular-nums"
        }
      }
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          borderRadius: 16,
          backgroundColor: "#FFF8EE"
        }
      }
    }
  }
});

export default theme;
