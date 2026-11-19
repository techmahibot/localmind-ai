import React from "react";
import ReactDOM from "react-dom/client";
// Fonts are bundled with the app so it looks the same with no internet.
import "@fontsource/nunito/latin-400.css";
import "@fontsource/nunito/latin-600.css";
import "@fontsource/nunito/latin-700.css";
import "@fontsource/fraunces/latin-600.css";
import "@fontsource/fraunces/latin-600-italic.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import App from "./App.jsx";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
