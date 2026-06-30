import axios from "axios";

const apiHost = window.location.hostname || "127.0.0.1";

export default axios.create({
  baseURL: `http://${apiHost}:8000`,
  withCredentials: true,
});
