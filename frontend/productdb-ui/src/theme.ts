import { createTheme } from "@mui/material/styles";

const theme = createTheme({
  palette: {
    mode: "light",
    primary: {
      main: "#0f766e"
    },
    secondary: {
      main: "#2563eb"
    },
    background: {
      default: "#f5f7fa",
      paper: "#ffffff"
    }
  },
  typography: {
    fontFamily: "'Inter', 'Segoe UI', 'Roboto', 'Helvetica', 'Arial', sans-serif",
    h4: {
      fontWeight: 600
    }
  },
  shape: {
    borderRadius: 12
  },
  components: {
    MuiPaper: {
      styleOverrides: {
        root: {
          borderRadius: 16
        }
      }
    }
  }
});

export default theme;
