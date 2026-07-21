import axios from "axios";

const apiBaseUrl = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

export default axios.create({
  baseURL: apiBaseUrl,
  withCredentials: true,
});
