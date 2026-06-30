import { useEffect, useMemo, useState } from "react";
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

function formatYears(value) {
  if (value === null || value === undefined || value === "" || Number(value) <= 0) {
    return "Not found";
  }

  return value;
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
    }
  }, [user]);

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
        <div>
          <h1>Resume Parser Dashboard</h1>
          <p className="subtitle">Signed in as {user.email}</p>
        </div>

        <div className="toolbar-actions">
          <button className="secondary-button" onClick={loadCandidates} disabled={isLoadingCandidates}>
            {isLoadingCandidates ? "Refreshing..." : "Refresh"}
          </button>
          <button className="secondary-button" onClick={handleLogout}>
            Sign Out
          </button>
        </div>
      </section>

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
                    <td>{formatValue(candidate.normalized_skills || candidate.skills)}</td>
                    <td>{formatValue(candidate.resume_summary)}</td>
                    <td>{formatValue(candidate.filename)}</td>
                    <td>{formatValue(candidate.created_at)}</td>
                    <td>
                      <div className="row-actions">
                        <button
                          className="view-button"
                          type="button"
                          onClick={() => setSelectedCandidate(candidate)}
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
