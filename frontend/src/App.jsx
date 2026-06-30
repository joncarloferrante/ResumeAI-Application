import { useCallback, useEffect, useMemo, useState } from "react";
import api from "./api";
import "./App.css";

const searchFields = [
  "candidate",
  "email",
  "phone",
  "current_position",
  "current_company",
  "skills",
  "normalized_skills",
  "resume_summary",
  "filename",
];

const sortAccessors = {
  candidate: (candidate) => candidate.candidate ?? "",
  experience: (candidate) => Number(candidate.total_experience_years) || 0,
  created_at: (candidate) => candidate.created_at ?? "",
};

const auditActionOptions = [
  "All Actions",
  "Login",
  "Logout",
  "Resume Upload",
  "Candidate Delete",
  "Candidate View",
  "Audit Logs View",
];

const auditStatusOptions = ["All Statuses", "Success", "Failed", "Denied", "Warning"];

const emptyAnalytics = {
  summary: {
    total_candidates: 0,
    uploaded_today: 0,
    needs_review: 0,
    average_experience_years: null,
    average_career_span_years: null,
  },
  top_skills: [],
  top_roles: [],
  recent_uploads: [],
};

const missingValueTokens = new Set([
  "none",
  "not found",
  "email not found",
  "phone not found",
  "skills not found",
  "date not found",
  "no current position",
  "candidate name unknown",
  "current position unknown",
  "company unknown",
]);

function formatValue(value) {
  if (value === null || value === undefined || value === "") {
    return "Not found";
  }

  if (typeof value === "string" && missingValueTokens.has(value.trim().toLowerCase())) {
    return "Not found";
  }

  return value;
}

function formatPreview(value, maxLength = 110) {
  const formattedValue = formatValue(value);
  const textValue = String(formattedValue);

  if (textValue.length <= maxLength) {
    return textValue;
  }

  return `${textValue.slice(0, maxLength).trimEnd()}...`;
}

function formatYears(value) {
  if (value === null || value === undefined || value === "" || Number(value) <= 0) {
    return "Not found";
  }

  return value;
}

function formatMetric(value, fractionDigits = 0) {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) {
    return "0";
  }

  return numberValue.toLocaleString(undefined, {
    maximumFractionDigits: fractionDigits,
    minimumFractionDigits: fractionDigits,
  });
}

function compareValues(firstValue, secondValue) {
  if (typeof firstValue === "number" && typeof secondValue === "number") {
    return firstValue - secondValue;
  }

  return String(firstValue).localeCompare(String(secondValue), undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

function formatAuditDetails(details) {
  if (!details) {
    return "";
  }

  try {
    const parsedDetails = JSON.parse(details);
    if (parsedDetails && typeof parsedDetails === "object" && !Array.isArray(parsedDetails)) {
      return Object.entries(parsedDetails)
        .map(([key, value]) => `${key}: ${value}`)
        .join(", ");
    }

    return String(parsedDetails);
  } catch {
    return details;
  }
}

function getAuditStatus(status) {
  return String(status || "").trim().toLowerCase();
}

function getAuditDate(timestamp) {
  return String(timestamp || "").slice(0, 10);
}

function CompanyLogo({ onClick }) {
  return (
    <button className="company-logo-button" type="button" onClick={onClick} aria-label="Go to Analytics">
      <img src="/atlantic-group-logo.svg" alt="Atlantic Group" />
    </button>
  );
}

function DetailItem({ label, value }) {
  return (
    <div className="detail-item">
      <dt>{label}</dt>
      <dd>{formatValue(value)}</dd>
    </div>
  );
}

function App() {
  const [user, setUser] = useState(null);
  const [authForm, setAuthForm] = useState({ email: "", password: "" });
  const [authError, setAuthError] = useState("");
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [isSubmittingAuth, setIsSubmittingAuth] = useState(false);
  const [file, setFile] = useState(null);
  const [message, setMessage] = useState("");
  const [candidates, setCandidates] = useState([]);
  const [isUploading, setIsUploading] = useState(false);
  const [isLoadingCandidates, setIsLoadingCandidates] = useState(false);
  const [candidateError, setCandidateError] = useState("");
  const [candidateStatus, setCandidateStatus] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [sortConfig, setSortConfig] = useState({ key: null, direction: "asc" });
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [deletingCandidateId, setDeletingCandidateId] = useState(null);
  const [activePage, setActivePage] = useState("candidates");
  const [auditLogs, setAuditLogs] = useState([]);
  const [isLoadingAuditLogs, setIsLoadingAuditLogs] = useState(false);
  const [auditLogError, setAuditLogError] = useState("");
  const [analytics, setAnalytics] = useState(emptyAnalytics);
  const [isLoadingAnalytics, setIsLoadingAnalytics] = useState(false);
  const [analyticsError, setAnalyticsError] = useState("");
  const [auditFilters, setAuditFilters] = useState({
    query: "",
    action: "All Actions",
    status: "All Statuses",
    startDate: "",
    endDate: "",
  });
  const isAdmin = user?.role === "admin";

  // Keep search and sort client-side so the existing API contract stays unchanged.
  const visibleCandidates = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();

    const filteredCandidates = normalizedQuery
      ? candidates.filter((candidate) =>
          searchFields.some((field) =>
            String(candidate[field] ?? "")
              .toLowerCase()
              .includes(normalizedQuery),
          ),
        )
      : candidates;

    if (!sortConfig.key) {
      return filteredCandidates;
    }

    const getSortValue = sortAccessors[sortConfig.key];
    const directionMultiplier = sortConfig.direction === "asc" ? 1 : -1;

    return [...filteredCandidates].sort(
      (firstCandidate, secondCandidate) =>
        compareValues(getSortValue(firstCandidate), getSortValue(secondCandidate)) *
        directionMultiplier,
    );
  }, [candidates, searchQuery, sortConfig]);

  const visibleAuditLogs = useMemo(() => {
    const normalizedQuery = auditFilters.query.trim().toLowerCase();
    const selectedAction = auditFilters.action === "All Actions" ? "" : auditFilters.action;
    const selectedStatus =
      auditFilters.status === "All Statuses" ? "" : auditFilters.status.toLowerCase();

    return [...auditLogs]
      .filter((log) => {
        const details = formatAuditDetails(log.details);
        const status = getAuditStatus(log.status);
        const logDate = getAuditDate(log.timestamp);
        const matchesQuery =
          !normalizedQuery ||
          [log.user_email, log.action, details, log.status].some((value) =>
            String(value ?? "")
              .toLowerCase()
              .includes(normalizedQuery),
          );
        const matchesAction = !selectedAction || log.action === selectedAction;
        const matchesStatus = !selectedStatus || status === selectedStatus;
        const matchesStartDate = !auditFilters.startDate || logDate >= auditFilters.startDate;
        const matchesEndDate = !auditFilters.endDate || logDate <= auditFilters.endDate;

        return (
          matchesQuery &&
          matchesAction &&
          matchesStatus &&
          matchesStartDate &&
          matchesEndDate
        );
      })
      .sort((firstLog, secondLog) => {
        const firstId = Number(firstLog.id) || 0;
        const secondId = Number(secondLog.id) || 0;
        return secondId - firstId;
      });
  }, [auditFilters, auditLogs]);

  const hasAuditFilters = Object.values(auditFilters).some(
    (value) => value && value !== "All Actions" && value !== "All Statuses",
  );

  const analyticsCards = [
    {
      label: "Total Candidates",
      value: formatMetric(analytics.summary.total_candidates),
    },
    {
      label: "Candidates Uploaded Today",
      value: formatMetric(analytics.summary.uploaded_today),
    },
    {
      label: "Candidates Needing Review",
      value: formatMetric(analytics.summary.needs_review),
    },
    {
      label: "Average Experience (Years)",
      value: formatMetric(analytics.summary.average_experience_years, 1),
    },
    {
      label: "Average Career Span (Years)",
      value: formatMetric(analytics.summary.average_career_span_years, 1),
    },
  ];

  const maxSkillCount = Math.max(...analytics.top_skills.map((skill) => skill.count), 1);
  const maxRoleCount = Math.max(...analytics.top_roles.map((role) => role.count), 1);

  const handleSort = (key) => {
    setSortConfig((currentSort) => ({
      key,
      direction: currentSort.key === key && currentSort.direction === "asc" ? "desc" : "asc",
    }));
  };

  const getSortIndicator = (key) => {
    if (sortConfig.key !== key) {
      return "";
    }

    return sortConfig.direction === "asc" ? " ▲" : " ▼";
  };

  const closeCandidateDetails = () => {
    setSelectedCandidate(null);
  };

  const updateAuditFilter = (key, value) => {
    setAuditFilters((currentFilters) => ({
      ...currentFilters,
      [key]: value,
    }));
  };

  const clearAuditFilters = () => {
    setAuditFilters({
      query: "",
      action: "All Actions",
      status: "All Statuses",
      startDate: "",
      endDate: "",
    });
  };

  const loadAuditLogs = useCallback(async () => {
    if (!isAdmin) {
      return;
    }

    setIsLoadingAuditLogs(true);
    setAuditLogError("");

    try {
      const response = await api.get("/audit-logs");
      setAuditLogs(response.data);
    } catch (error) {
      console.error(error);
      if (error.response?.status === 401) {
        setUser(null);
        setAuditLogError("");
      } else if (error.response?.status === 403) {
        setAuditLogError("Only admins can view audit logs.");
      } else {
        setAuditLogError("Could not load audit logs.");
      }
    } finally {
      setIsLoadingAuditLogs(false);
    }
  }, [isAdmin]);

  const loadAnalytics = useCallback(async () => {
    setIsLoadingAnalytics(true);
    setAnalyticsError("");

    try {
      const response = await api.get("/analytics");
      setAnalytics(response.data);
    } catch (error) {
      console.error(error);
      if (error.response?.status === 401) {
        setUser(null);
        setAnalyticsError("");
      } else {
        setAnalyticsError("Could not load analytics.");
      }
    } finally {
      setIsLoadingAnalytics(false);
    }
  }, []);

  const loadCandidates = async () => {
    setIsLoadingCandidates(true);
    setCandidateError("");
    setCandidateStatus("");

    try {
      const response = await api.get("/candidates");
      setCandidates(response.data);
    } catch (error) {
      console.error(error);
      if (error.response?.status === 401) {
        setUser(null);
        setCandidateError("");
      } else {
        setCandidateError("Could not load saved candidates.");
      }
    } finally {
      setIsLoadingCandidates(false);
    }
  };

  useEffect(() => {
    const checkAuth = async () => {
      try {
        const response = await api.get("/auth/me");
        setUser(response.data.user);
      } catch {
        setUser(null);
      } finally {
        setIsCheckingAuth(false);
      }
    };

    checkAuth();
  }, []);

  useEffect(() => {
    if (user) {
      loadCandidates();
    } else {
      setCandidates([]);
      setSelectedCandidate(null);
      setAuditLogs([]);
      setAnalytics(emptyAnalytics);
      setActivePage("candidates");
    }
  }, [user]);

  useEffect(() => {
    if (activePage === "analytics" && user) {
      loadAnalytics();
    }
  }, [activePage, loadAnalytics, user]);

  useEffect(() => {
    if (activePage === "auditLogs" && user && !isAdmin) {
      setActivePage("analytics");
    }
  }, [activePage, isAdmin, user]);

  useEffect(() => {
    if (activePage === "auditLogs" && isAdmin) {
      loadAuditLogs();
    }
  }, [activePage, isAdmin, loadAuditLogs]);

  useEffect(() => {
    if (!selectedCandidate) {
      return undefined;
    }

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        closeCandidateDetails();
      }
    };

    window.addEventListener("keydown", handleKeyDown);

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [selectedCandidate]);

  const handleAuthSubmit = async (event) => {
    event.preventDefault();
    setAuthError("");
    setIsSubmittingAuth(true);

    try {
      const response = await api.post("/auth/login", authForm);
      setUser(response.data.user);
      setAuthForm({ email: "", password: "" });
    } catch (error) {
      const detail = error.response?.data?.detail;
      setAuthError(detail || "Authentication failed.");
    } finally {
      setIsSubmittingAuth(false);
    }
  };

  const handleLogout = async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setUser(null);
      setFile(null);
      setMessage("");
      setCandidates([]);
      setAuditLogs([]);
      setAnalytics(emptyAnalytics);
      setActivePage("candidates");
    }
  };

  const handleUpload = async () => {
    if (!file) {
      setMessage("Please select a resume first.");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);

    setIsUploading(true);
    setMessage("");

    try {
      const response = await api.post("/upload", formData, {
        headers: {
          "Content-Type": "multipart/form-data",
        },
      });

      setMessage(`Saved candidate #${response.data.candidate_id}: ${file.name}`);
      setFile(null);
      await loadCandidates();
    } catch (error) {
      console.error(error);
      const detail = error.response?.data?.detail;
      if (error.response?.status === 401) {
        setUser(null);
        setMessage("Your session expired. Please sign in again.");
      } else if (error.response?.status === 409 && detail) {
        setMessage(detail);
      } else {
        setMessage(detail ? `Upload failed: ${detail}` : "Upload failed.");
      }
    } finally {
      setIsUploading(false);
    }
  };

  const handleViewCandidate = async (candidate) => {
    setCandidateError("");

    try {
      const response = await api.get(`/candidates/${candidate.id}`);
      setSelectedCandidate(response.data);
    } catch (error) {
      console.error(error);
      if (error.response?.status === 401) {
        setUser(null);
      } else {
        const detail = error.response?.data?.detail;
        setCandidateError(detail || "Could not load candidate details.");
      }
    }
  };

  const handleDeleteCandidate = async (candidate) => {
    const confirmed = window.confirm("Are you sure you want to delete this candidate?");
    if (!confirmed) {
      return;
    }

    setCandidateError("");
    setCandidateStatus("");
    setDeletingCandidateId(candidate.id);

    try {
      // Admin-only deletion is still enforced by the backend; this call only runs
      // when the signed-in user role allows showing the delete control.
      await api.delete(`/candidates/${candidate.id}`);
      setCandidates((currentCandidates) =>
        currentCandidates.filter((currentCandidate) => currentCandidate.id !== candidate.id),
      );
      if (selectedCandidate?.id === candidate.id) {
        setSelectedCandidate(null);
      }
      setCandidateStatus("Candidate deleted successfully.");
    } catch (error) {
      console.error(error);
      if (error.response?.status === 403) {
        setCandidateError("Only admins can delete candidates.");
      } else {
        const detail = error.response?.data?.detail;
        setCandidateError(detail || "Could not delete candidate.");
      }
    } finally {
      setDeletingCandidateId(null);
    }
  };

  if (isCheckingAuth) {
    return (
      <main className="auth-page">
        <section className="auth-panel">
          <img className="auth-logo" src="/atlantic-group-logo.svg" alt="Atlantic Group" />
          <h1>ResumeAI</h1>
          <p className="subtitle">Checking your session...</p>
        </section>
      </main>
    );
  }

  if (!user) {
    return (
      <main className="auth-page">
        <section className="auth-panel">
          <div>
            <img className="auth-logo" src="/atlantic-group-logo.svg" alt="Atlantic Group" />
            <h1>ResumeAI</h1>
            <p className="subtitle">Sign in to access the Resume Parser Dashboard.</p>
          </div>

          <form className="auth-form" onSubmit={handleAuthSubmit}>
            <label>
              <span>Email</span>
              <input
                type="email"
                value={authForm.email}
                onChange={(event) => setAuthForm({ ...authForm, email: event.target.value })}
                autoComplete="email"
                required
              />
            </label>

            <label>
              <span>Password</span>
              <input
                type="password"
                value={authForm.password}
                onChange={(event) => setAuthForm({ ...authForm, password: event.target.value })}
                autoComplete="current-password"
                required
              />
            </label>

            {authError && <p className="error-message">{authError}</p>}

            <button className="primary-button" type="submit" disabled={isSubmittingAuth}>
              {isSubmittingAuth ? "Please wait..." : "Sign In"}
            </button>
          </form>
        </section>
      </main>
    );
  }

  return (
    <main className="dashboard">
      <section className="toolbar">
        <div className="toolbar-brand">
          <CompanyLogo onClick={() => setActivePage("analytics")} />
          <div>
            <h1>Resume Parser Dashboard</h1>
            <p className="subtitle">Signed in as {user.email}</p>
          </div>
        </div>

        <div className="toolbar-actions">
          {activePage !== "candidates" && (
            <button className="secondary-button" onClick={() => setActivePage("candidates")}>
              Candidates
            </button>
          )}
          {activePage !== "analytics" && (
            <button className="secondary-button" onClick={() => setActivePage("analytics")}>
              Analytics
            </button>
          )}
          {isAdmin && (
            <button
              className="secondary-button"
              onClick={() => setActivePage("auditLogs")}
              disabled={activePage === "auditLogs"}
            >
              Audit Logs
            </button>
          )}
          {activePage === "auditLogs" ? (
            <button className="secondary-button" onClick={loadAuditLogs} disabled={isLoadingAuditLogs}>
              {isLoadingAuditLogs ? "Refreshing..." : "Refresh"}
            </button>
          ) : activePage === "analytics" ? (
            <button className="secondary-button" onClick={loadAnalytics} disabled={isLoadingAnalytics}>
              {isLoadingAnalytics ? "Refreshing..." : "Refresh"}
            </button>
          ) : (
            <button className="secondary-button" onClick={loadCandidates} disabled={isLoadingCandidates}>
              {isLoadingCandidates ? "Refreshing..." : "Refresh"}
            </button>
          )}
          <button className="secondary-button" onClick={handleLogout}>
            Sign Out
          </button>
        </div>
      </section>

      {activePage === "candidates" ? (
        <>
          <section className="upload-panel">
            <label className="file-picker">
              <span>Resume file</span>
              <input
                type="file"
                accept=".pdf,.doc,.docx"
                onChange={(event) => {
                  setFile(event.target.files[0] ?? null);
                  setMessage("");
                }}
              />
            </label>

            <button className="primary-button" onClick={handleUpload} disabled={isUploading}>
              {isUploading ? "Uploading..." : "Upload Resume"}
            </button>

            <p className="status-message">
              {message || (file ? `Selected: ${file.name}` : "No resume selected.")}
            </p>
          </section>

          <section className="records-section">
            <div className="records-heading">
              <h2>Saved Candidates</h2>
              <span>{candidates.length} records</span>
            </div>

            <label className="search-field">
              <span>Search candidates</span>
              <input
                type="search"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="Search candidates..."
              />
            </label>

            {candidateError && <p className="error-message">{candidateError}</p>}
            {candidateStatus && <p className="success-message">{candidateStatus}</p>}

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>
                      <button className="sort-button" type="button" onClick={() => handleSort("candidate")}>
                        Candidate{getSortIndicator("candidate")}
                      </button>
                    </th>
                    <th>Email</th>
                    <th>Phone</th>
                    <th>Current Role</th>
                    <th>Company</th>
                    <th>
                      <button className="sort-button" type="button" onClick={() => handleSort("experience")}>
                        Experience{getSortIndicator("experience")}
                      </button>
                    </th>
                    <th>Career Span</th>
                    <th>Skills</th>
                    <th>Summary</th>
                    <th>Filename</th>
                    <th>
                      <button className="sort-button" type="button" onClick={() => handleSort("created_at")}>
                        Created{getSortIndicator("created_at")}
                      </button>
                    </th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.length === 0 ? (
                    <tr>
                      <td className="empty-state" colSpan="13">
                        {isLoadingCandidates ? "Loading candidates..." : "No candidates saved yet."}
                      </td>
                    </tr>
                  ) : visibleCandidates.length === 0 ? (
                    <tr>
                      <td className="empty-state" colSpan="13">
                        No matching candidates found.
                      </td>
                    </tr>
                  ) : (
                    visibleCandidates.map((candidate) => (
                      <tr key={candidate.id}>
                        <td>{candidate.id}</td>
                        <td>{formatValue(candidate.candidate)}</td>
                        <td>{formatValue(candidate.email)}</td>
                        <td>{formatValue(candidate.phone)}</td>
                        <td>{formatValue(candidate.current_position)}</td>
                        <td>{formatValue(candidate.current_company)}</td>
                        <td>{formatYears(candidate.total_experience_years)}</td>
                        <td>{formatYears(candidate.career_span_years)}</td>
                        <td className="preview-cell" title={formatValue(candidate.normalized_skills || candidate.skills)}>
                          {formatPreview(candidate.normalized_skills || candidate.skills)}
                        </td>
                        <td className="preview-cell" title={formatValue(candidate.resume_summary)}>
                          {formatPreview(candidate.resume_summary)}
                        </td>
                        <td>{formatValue(candidate.filename)}</td>
                        <td>{formatValue(candidate.created_at)}</td>
                        <td>
                          <div className="row-actions">
                            <button
                              className="view-button"
                              type="button"
                              onClick={() => handleViewCandidate(candidate)}
                            >
                              View
                            </button>
                            {isAdmin && (
                              <button
                                className="delete-button"
                                type="button"
                                onClick={() => handleDeleteCandidate(candidate)}
                                disabled={deletingCandidateId === candidate.id}
                              >
                                {deletingCandidateId === candidate.id ? "Deleting..." : "Delete"}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : activePage === "auditLogs" ? (
        <section className="records-section audit-section">
          <div className="records-heading">
            <h2>Audit Logs</h2>
            <span>
              {visibleAuditLogs.length} of {auditLogs.length} records
            </span>
          </div>

          <div className="audit-filters">
            <label className="audit-filter audit-search-field">
              <span>Search logs</span>
              <input
                type="search"
                value={auditFilters.query}
                onChange={(event) => updateAuditFilter("query", event.target.value)}
                placeholder="Search user, action, details, or status..."
              />
            </label>

            <label className="audit-filter">
              <span>Action</span>
              <select
                value={auditFilters.action}
                onChange={(event) => updateAuditFilter("action", event.target.value)}
              >
                {auditActionOptions.map((action) => (
                  <option key={action} value={action}>
                    {action}
                  </option>
                ))}
              </select>
            </label>

            <label className="audit-filter">
              <span>Status</span>
              <select
                value={auditFilters.status}
                onChange={(event) => updateAuditFilter("status", event.target.value)}
              >
                {auditStatusOptions.map((status) => (
                  <option key={status} value={status}>
                    {status}
                  </option>
                ))}
              </select>
            </label>

            <label className="audit-filter">
              <span>Start Date</span>
              <input
                type="date"
                value={auditFilters.startDate}
                onChange={(event) => updateAuditFilter("startDate", event.target.value)}
              />
            </label>

            <label className="audit-filter">
              <span>End Date</span>
              <input
                type="date"
                value={auditFilters.endDate}
                onChange={(event) => updateAuditFilter("endDate", event.target.value)}
              />
            </label>

            <button
              className="secondary-button clear-filters-button"
              type="button"
              onClick={clearAuditFilters}
              disabled={!hasAuditFilters}
            >
              Clear Filters
            </button>
          </div>

          {auditLogError && <p className="error-message">{auditLogError}</p>}

          <div className="table-wrap audit-table-wrap">
            <table className="audit-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>User</th>
                  <th>Action</th>
                  <th>Details</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {auditLogs.length === 0 ? (
                  <tr>
                    <td className="empty-state" colSpan="5">
                      {isLoadingAuditLogs ? "Loading audit logs..." : "No audit logs recorded yet."}
                    </td>
                  </tr>
                ) : visibleAuditLogs.length === 0 ? (
                  <tr>
                    <td className="empty-state" colSpan="5">
                      No audit logs match your filters.
                    </td>
                  </tr>
                ) : (
                  visibleAuditLogs.map((log) => (
                    <tr key={log.id}>
                      <td>{formatValue(log.timestamp)}</td>
                      <td>{formatValue(log.user_email)}</td>
                      <td>{formatValue(log.action)}</td>
                      <td>{formatValue(formatAuditDetails(log.details))}</td>
                      <td>
                        <span className={`status-pill status-${getAuditStatus(log.status)}`}>
                          {formatValue(getAuditStatus(log.status)).toUpperCase()}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      ) : (
        <section className="analytics-page">
          <div className="records-heading">
            <h2>Analytics</h2>
            <span>{isLoadingAnalytics ? "Refreshing..." : "Live database summary"}</span>
          </div>

          {analyticsError && <p className="error-message">{analyticsError}</p>}

          <div className="analytics-card-grid">
            {analyticsCards.map((card) => (
              <article className="analytics-card" key={card.label}>
                <span>{card.label}</span>
                <strong>{card.value}</strong>
              </article>
            ))}
          </div>

          <div className="analytics-grid">
            <section className="analytics-panel">
              <div className="records-heading">
                <h2>Top Skills</h2>
                <span>Top 10</span>
              </div>

              <div className="ranked-list">
                {analytics.top_skills.length === 0 ? (
                  <p className="empty-state">No normalized skills found yet.</p>
                ) : (
                  analytics.top_skills.map((skill) => (
                    <div className="ranked-item" key={skill.skill}>
                      <div>
                        <strong>{formatValue(skill.skill)}</strong>
                        <span>{skill.count} candidates</span>
                      </div>
                      <div className="rank-bar" aria-hidden="true">
                        <span style={{ width: `${Math.max((skill.count / maxSkillCount) * 100, 8)}%` }} />
                      </div>
                    </div>
                  ))
                )}
              </div>
            </section>

            <section className="analytics-panel">
              <div className="records-heading">
                <h2>Top Current Roles</h2>
                <span>Most common</span>
              </div>

              <div className="ranked-list">
                {analytics.top_roles.length === 0 ? (
                  <p className="empty-state">No current roles found yet.</p>
                ) : (
                  analytics.top_roles.map((role) => (
                    <div className="ranked-item" key={role.role}>
                      <div>
                        <strong>{formatValue(role.role)}</strong>
                        <span>{role.count} candidates</span>
                      </div>
                      <div className="rank-bar" aria-hidden="true">
                        <span style={{ width: `${Math.max((role.count / maxRoleCount) * 100, 8)}%` }} />
                      </div>
                    </div>
                  ))
                )}
              </div>
            </section>
          </div>

          <section className="records-section">
            <div className="records-heading">
              <h2>Recent Uploads</h2>
              <span>Latest 5</span>
            </div>

            <div className="table-wrap analytics-table-wrap">
              <table className="analytics-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Candidate</th>
                    <th>Email</th>
                    <th>Current Role</th>
                    <th>Filename</th>
                    <th>Uploaded</th>
                  </tr>
                </thead>
                <tbody>
                  {analytics.recent_uploads.length === 0 ? (
                    <tr>
                      <td className="empty-state" colSpan="6">
                        No recent uploads yet.
                      </td>
                    </tr>
                  ) : (
                    analytics.recent_uploads.map((candidate) => (
                      <tr key={candidate.id}>
                        <td>{candidate.id}</td>
                        <td>{formatValue(candidate.candidate)}</td>
                        <td>{formatValue(candidate.email)}</td>
                        <td>{formatValue(candidate.current_position)}</td>
                        <td>{formatValue(candidate.filename)}</td>
                        <td>{formatValue(candidate.created_at)}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </section>
      )}

      {selectedCandidate && (
        <div className="modal-overlay" onClick={closeCandidateDetails}>
          <section
            className="details-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="candidate-details-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="modal-header">
              <div>
                <h2 id="candidate-details-title">Candidate Details</h2>
                <p>{formatValue(selectedCandidate.candidate)}</p>
              </div>

              <button className="modal-close" type="button" onClick={closeCandidateDetails}>
                Close
              </button>
            </header>

            <div className="details-grid">
              <section className="detail-card">
                <h3>Personal Information</h3>
                <dl>
                  <DetailItem label="Candidate name" value={selectedCandidate.candidate} />
                  <DetailItem label="Email" value={selectedCandidate.email} />
                  <DetailItem label="Phone" value={selectedCandidate.phone} />
                </dl>
              </section>

              <section className="detail-card">
                <h3>Employment</h3>
                <dl>
                  <DetailItem label="Current Role" value={selectedCandidate.current_position} />
                  <DetailItem label="Company" value={selectedCandidate.current_company} />
                  <DetailItem label="Experience" value={formatYears(selectedCandidate.total_experience_years)} />
                  <DetailItem label="Career span" value={formatYears(selectedCandidate.career_span_years)} />
                </dl>
              </section>

              <section className="detail-card">
                <h3>Skills</h3>
                <dl>
                  <DetailItem label="Skills" value={selectedCandidate.skills} />
                  <DetailItem label="Normalized skills" value={selectedCandidate.normalized_skills} />
                </dl>
              </section>

              <section className="detail-card">
                <h3>Resume</h3>
                <dl>
                  <DetailItem label="Filename" value={selectedCandidate.filename} />
                  <DetailItem label="Created/upload date" value={selectedCandidate.created_at} />
                  <DetailItem label="Summary" value={selectedCandidate.resume_summary} />
                </dl>
              </section>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}

export default App;
