import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

const emptySecurityDashboard = {
  summary: {
    total_users: 0,
    admin_users: 0,
    recruiter_users: 0,
    locked_accounts: 0,
    total_candidates: 0,
    total_resume_uploads: 0,
    audit_events_today: 0,
    failed_login_attempts: 0,
  },
  recent_activity: [],
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

const recruiterNavigationItems = [
  { key: "candidates", label: "Candidates", icon: CandidatesIcon },
  { key: "upload", label: "Upload Resume", icon: UploadIcon },
  { key: "analytics", label: "Analytics", icon: AnalyticsIcon },
  { key: "jobBoard", label: "Job Board", icon: JobBoardIcon },
];

const adminNavigationItems = [
  ...recruiterNavigationItems,
  { key: "securityDashboard", label: "Security Dashboard", icon: SecurityIcon },
  { key: "users", label: "Users", icon: UsersIcon },
];

const recruiterAllowedPages = new Set(recruiterNavigationItems.map((item) => item.key));
const adminAllowedPages = new Set(adminNavigationItems.map((item) => item.key));

function formatValue(value) {
  if (value === null || value === undefined || value === "") {
    return "Not found";
  }

  if (typeof value === "string" && missingValueTokens.has(value.trim().toLowerCase())) {
    return "Not found";
  }

  return String(value);
}

function formatYears(value) {
  if (value === null || value === undefined || value === "" || Number(value) <= 0) {
    return "Not found";
  }

  return String(value);
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

function formatRefreshTimestamp(date) {
  if (!(date instanceof Date)) {
    return "Last Refresh --/--/-- --:-- --";
  }

  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const year = String(date.getFullYear()).slice(-2);
  const hours24 = date.getHours();
  const hours12 = hours24 % 12 || 12;
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const meridiem = hours24 >= 12 ? "PM" : "AM";

  return `Last Refresh ${month}/${day}/${year} ${hours12}:${minutes} ${meridiem}`;
}

function getUploadFileExtension(file) {
  return String(file?.name || "")
    .split(".")
    .pop()
    .toLowerCase();
}

function isSupportedResumeFile(file) {
  return ["pdf", "doc", "docx"].includes(getUploadFileExtension(file));
}

function AppIcon({ children, className = "" }) {
  return (
    <svg
      className={`app-icon ${className}`.trim()}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

function CandidatesIcon() {
  return (
    <AppIcon>
      <path d="M9.5 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4Z" />
      <path d="M3.5 20a6 6 0 0 1 12 0" />
      <path d="M16.5 12a3.25 3.25 0 1 0-2.2-5.64" />
      <path d="M15 20a5.7 5.7 0 0 0-1.2-3.4" />
    </AppIcon>
  );
}

function UploadIcon() {
  return (
    <AppIcon>
      <path d="M12 16V5" />
      <path d="m7.5 9.5 4.5-4.5 4.5 4.5" />
      <path d="M4.5 19.5h15" />
    </AppIcon>
  );
}

function AnalyticsIcon() {
  return (
    <AppIcon>
      <path d="M5 19.5h14" />
      <path d="M6.5 17.5v-5" />
      <path d="M11 17.5v-9" />
      <path d="M15.5 17.5v-7" />
      <path d="M6 11.5 10.5 8l3.5 2 4.5-5" />
    </AppIcon>
  );
}

function JobBoardIcon() {
  return (
    <AppIcon>
      <path d="M4.5 8h15v11a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1V8Z" />
      <path d="M8 8V6.5A1.5 1.5 0 0 1 9.5 5h5A1.5 1.5 0 0 1 16 6.5V8" />
      <path d="M4.5 12h15" />
    </AppIcon>
  );
}

function AuditIcon() {
  return (
    <AppIcon>
      <path d="M7 4.5h7l3 3V19a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V5.5a1 1 0 0 1 1-1Z" />
      <path d="M9 10.5h6" />
      <path d="M9 14h6" />
      <path d="M10 4.5V8h3.5" />
    </AppIcon>
  );
}

function SecurityIcon() {
  return (
    <AppIcon>
      <path d="M12 4.5 18 7v5.2c0 4-2.7 6.8-6 8.3-3.3-1.5-6-4.3-6-8.3V7l6-2.5Z" />
      <path d="M9.5 12.2 11.2 14l3.3-3.5" />
    </AppIcon>
  );
}

function UsersIcon() {
  return (
    <AppIcon>
      <path d="M9.5 11.5a3.5 3.5 0 1 0-3.5-3.5 3.5 3.5 0 0 0 3.5 3.5Z" />
      <path d="M3.5 20a6 6 0 0 1 12 0" />
      <path d="M16.5 11a3 3 0 1 0-2-5.2" />
      <path d="M15 20a5.6 5.6 0 0 0-1.1-3.2" />
    </AppIcon>
  );
}

function UserPlusIcon() {
  return (
    <AppIcon>
      <path d="M20 7.5v5" />
      <path d="M17.5 10h5" />
      <path d="M9.5 11.5a3.5 3.5 0 1 0-3.5-3.5 3.5 3.5 0 0 0 3.5 3.5Z" />
      <path d="M3.5 20a6 6 0 0 1 12 0" />
    </AppIcon>
  );
}

function KeyIcon() {
  return (
    <AppIcon>
      <circle cx="8.25" cy="8.25" r="3.25" />
      <path d="M10.8 10.8 14.5 14.5" />
      <path d="M13.5 11.5h2l1 1-1 1h-1l-1 1" />
    </AppIcon>
  );
}

function LockIcon() {
  return (
    <AppIcon>
      <rect x="5.5" y="10" width="13" height="9" rx="2" />
      <path d="M8.5 10V7.5a3.5 3.5 0 0 1 7 0V10" />
    </AppIcon>
  );
}

function UnlockIcon() {
  return (
    <AppIcon>
      <rect x="5.5" y="10" width="13" height="9" rx="2" />
      <path d="M8.5 10V7.5a3.5 3.5 0 0 1 6.2-2.2" />
      <path d="M12 13v2.5" />
    </AppIcon>
  );
}

function TrashIcon() {
  return (
    <AppIcon>
      <path d="M5.5 7.5h13" />
      <path d="M9 7.5V5.8A1.3 1.3 0 0 1 10.3 4.5h3.4A1.3 1.3 0 0 1 15 5.8v1.7" />
      <path d="M9 11v5" />
      <path d="M12 11v5" />
      <path d="M7 7.5 7.6 18a1 1 0 0 0 1 1h6.8a1 1 0 0 0 1-1l.6-10.5" />
    </AppIcon>
  );
}

function RefreshIcon() {
  return (
    <AppIcon>
      <path d="M20 12a8 8 0 0 1-13.2 6" />
      <path d="M4 12a8 8 0 0 1 13.2-6" />
      <path d="M7 18H4v3" />
      <path d="M20 6V3h-3" />
    </AppIcon>
  );
}

function SearchIcon() {
  return (
    <AppIcon>
      <circle cx="11" cy="11" r="5.5" />
      <path d="m16 16 4 4" />
    </AppIcon>
  );
}

function ChevronLeftIcon() {
  return (
    <AppIcon>
      <path d="m14 6-6 6 6 6" />
    </AppIcon>
  );
}

function ChevronRightIcon() {
  return (
    <AppIcon>
      <path d="m10 6 6 6-6 6" />
    </AppIcon>
  );
}

function TableValue({ value, className = "" }) {
  const text = formatValue(value);

  return (
    <span className={`table-value ${className}`.trim()} title={text}>
      {text}
    </span>
  );
}

function MetricCard({ label, value }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function App() {
  const [user, setUser] = useState(null);
  const [authForm, setAuthForm] = useState({ email: "", password: "" });
  const [authError, setAuthError] = useState("");
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const [isSubmittingAuth, setIsSubmittingAuth] = useState(false);
  const [files, setFiles] = useState([]);
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
  const [users, setUsers] = useState([]);
  const [isLoadingUsers, setIsLoadingUsers] = useState(false);
  const [usersError, setUsersError] = useState("");
  const [lastUsersRefresh, setLastUsersRefresh] = useState(null);
  const [userSearchQuery, setUserSearchQuery] = useState("");
  const [currentUsersPage, setCurrentUsersPage] = useState(1);
  const [securityDashboard, setSecurityDashboard] = useState(emptySecurityDashboard);
  const [isLoadingSecurityDashboard, setIsLoadingSecurityDashboard] = useState(false);
  const [securityDashboardError, setSecurityDashboardError] = useState("");
  const [lastSecurityRefresh, setLastSecurityRefresh] = useState(null);
  const [selectedUser, setSelectedUser] = useState(null);
  const [userModalMode, setUserModalMode] = useState(null);
  const [userModalUser, setUserModalUser] = useState(null);
  const [userModalForm, setUserModalForm] = useState({
    name: "",
    username: "",
    email: "",
    role: "recruiter",
    password: "",
    confirmPassword: "",
  });
  const [userActionMessage, setUserActionMessage] = useState("");
  const [auditFilters, setAuditFilters] = useState({
    query: "",
    action: "All Actions",
    status: "All Statuses",
    startDate: "",
    endDate: "",
  });
  const [currentCandidatePage, setCurrentCandidatePage] = useState(1);
  const [lastCandidatesRefresh, setLastCandidatesRefresh] = useState(null);
  const [isDraggingUpload, setIsDraggingUpload] = useState(false);
  const uploadInputRef = useRef(null);

  const isAdmin = user?.role === "admin";
  const navigationItems = useMemo(
    () => (isAdmin ? adminNavigationItems : recruiterNavigationItems),
    [isAdmin],
  );
  const allowedPages = useMemo(
    () => (isAdmin ? adminAllowedPages : recruiterAllowedPages),
    [isAdmin],
  );
  const candidatesPerPage = 10;
  const safeActivePage = allowedPages.has(activePage) ? activePage : "candidates";

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

  const totalCandidatePages = Math.max(1, Math.ceil(visibleCandidates.length / candidatesPerPage));

  const paginatedCandidates = useMemo(() => {
    const startIndex = (currentCandidatePage - 1) * candidatesPerPage;
    return visibleCandidates.slice(startIndex, startIndex + candidatesPerPage);
  }, [currentCandidatePage, visibleCandidates]);

  const candidateStart = visibleCandidates.length === 0 ? 0 : (currentCandidatePage - 1) * candidatesPerPage + 1;
  const candidateEnd = Math.min(currentCandidatePage * candidatesPerPage, visibleCandidates.length);

  const usersPerPage = 7;
  const visibleUsers = useMemo(() => {
    const normalizedQuery = userSearchQuery.trim().toLowerCase();
    const filteredUsers = normalizedQuery
      ? users.filter((item) =>
          [item.name, item.username, item.email, item.role, item.last_login].some((value) =>
            String(value ?? "")
              .toLowerCase()
              .includes(normalizedQuery),
          ),
        )
      : users;

    return [...filteredUsers].sort((firstUser, secondUser) => {
      const firstRoleRank = firstUser.role === "admin" ? 0 : 1;
      const secondRoleRank = secondUser.role === "admin" ? 0 : 1;

      if (firstRoleRank !== secondRoleRank) {
        return firstRoleRank - secondRoleRank;
      }

      return compareValues(Number(firstUser.id) || 0, Number(secondUser.id) || 0);
    });
  }, [userSearchQuery, users]);

  const totalUserPages = Math.max(1, Math.ceil(visibleUsers.length / usersPerPage));
  const paginatedUsers = useMemo(() => {
    const startIndex = (currentUsersPage - 1) * usersPerPage;
    return visibleUsers.slice(startIndex, startIndex + usersPerPage);
  }, [currentUsersPage, visibleUsers]);

  const userStart = visibleUsers.length === 0 ? 0 : (currentUsersPage - 1) * usersPerPage + 1;
  const userEnd = Math.min(currentUsersPage * usersPerPage, visibleUsers.length);

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

  const handleUserRoleChange = async (targetUser, nextRole) => {
    if (!targetUser || targetUser.role === nextRole) {
      return;
    }

    const confirmed = window.confirm(`Change ${targetUser.username} to ${nextRole}?`);
    if (!confirmed) {
      return;
    }

    try {
      await api.patch(`/users/${targetUser.id}`, {
        name: targetUser.name,
        username: targetUser.username,
        email: targetUser.email,
        role: nextRole,
      });
      await loadUsers();
      if (targetUser.id === user?.id) {
        await refreshCurrentUser();
      }
    } catch (error) {
      console.error(error);
      const detail = error.response?.data?.detail;
      setUsersError(detail || "Could not change user role.");
    }
  };

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
    if (!user) {
      return false;
    }

    setIsLoadingAuditLogs(true);
    setAuditLogError("");

    try {
      const response = await api.get("/audit-logs");
      setAuditLogs(response.data);
      return true;
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
      return false;
    } finally {
      setIsLoadingAuditLogs(false);
    }
  }, [user]);

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

  const loadUsers = useCallback(async () => {
    setIsLoadingUsers(true);
    setUsersError("");

    try {
      const response = await api.get("/users");
      setUsers(response.data);
      setLastUsersRefresh(new Date());
      return true;
    } catch (error) {
      console.error(error);
      if (error.response?.status === 401) {
        setUser(null);
        setUsersError("");
      } else if (error.response?.status === 403) {
        setUsersError("Only admins can manage users.");
      } else {
        setUsersError("Could not load users.");
      }
      return false;
    } finally {
      setIsLoadingUsers(false);
    }
  }, []);

  const loadSecurityDashboard = useCallback(async () => {
    setIsLoadingSecurityDashboard(true);
    setSecurityDashboardError("");

    try {
      const response = await api.get("/security-dashboard");
      setSecurityDashboard(response.data);
      setLastSecurityRefresh(new Date());
      return true;
    } catch (error) {
      console.error(error);
      if (error.response?.status === 401) {
        setUser(null);
        setSecurityDashboardError("");
      } else if (error.response?.status === 403) {
        setSecurityDashboardError("Only admins can view the security dashboard.");
      } else {
        setSecurityDashboardError("Could not load security dashboard data.");
      }
      return false;
    } finally {
      setIsLoadingSecurityDashboard(false);
    }
  }, []);

  const refreshCurrentUser = useCallback(async () => {
    try {
      const response = await api.get("/auth/me");
      setUser(response.data.user);
    } catch (error) {
      console.error(error);
    }
  }, []);

  const loadCandidates = useCallback(async () => {
    setIsLoadingCandidates(true);
    setCandidateError("");
    setCandidateStatus("");

    try {
      const response = await api.get("/candidates");
      setCandidates(response.data);
      setLastCandidatesRefresh(new Date());
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
  }, []);

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
      setLastCandidatesRefresh(null);
      setLastUsersRefresh(null);
      setLastSecurityRefresh(null);
    }
  }, [user, loadCandidates]);

  useEffect(() => {
    if (!allowedPages.has(activePage)) {
      setActivePage("candidates");
    }
  }, [activePage, allowedPages]);

  useEffect(() => {
    if ((safeActivePage === "analytics") && user) {
      loadAnalytics();
    }
  }, [loadAnalytics, safeActivePage, user]);

  useEffect(() => {
    if (safeActivePage === "users" && user) {
      loadUsers();
    }
  }, [loadUsers, safeActivePage, user]);

  useEffect(() => {
    setCurrentUsersPage(1);
  }, [userSearchQuery, users]);

  useEffect(() => {
    if (currentUsersPage > totalUserPages) {
      setCurrentUsersPage(totalUserPages);
    }
  }, [currentUsersPage, totalUserPages]);

  useEffect(() => {
    if (safeActivePage === "securityDashboard" && user) {
      loadSecurityDashboard();
      loadAuditLogs();
    }
  }, [loadAuditLogs, loadSecurityDashboard, safeActivePage, user]);

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

  useEffect(() => {
    setCurrentCandidatePage(1);
  }, [candidates, searchQuery, sortConfig.key, sortConfig.direction]);

  useEffect(() => {
    if (currentCandidatePage > totalCandidatePages) {
      setCurrentCandidatePage(totalCandidatePages);
    }
  }, [currentCandidatePage, totalCandidatePages]);

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
      setFiles([]);
      setMessage("");
      setCandidates([]);
      setAuditLogs([]);
      setAnalytics(emptyAnalytics);
      setUsers([]);
      setSecurityDashboard(emptySecurityDashboard);
      setActivePage("candidates");
      setLastCandidatesRefresh(null);
      setLastUsersRefresh(null);
      setLastSecurityRefresh(null);
      setSelectedUser(null);
      setUserModalMode(null);
      setUserModalUser(null);
      setUserActionMessage("");
      setAuditLogError("");
    }
  };

  const handleUpload = async () => {
    if (files.length === 0) {
      setMessage("Please select at least one resume first.");
      return;
    }

    setIsUploading(true);
    setMessage("");

    try {
      const successes = [];
      const failures = [];

      for (const file of files) {
        const formData = new FormData();
        formData.append("file", file);

        try {
          const response = await api.post("/upload", formData, {
            headers: {
              "Content-Type": "multipart/form-data",
            },
          });

          successes.push(`Saved candidate #${response.data.candidate_id}: ${file.name}`);
        } catch (error) {
          console.error(error);
          const detail = error.response?.data?.detail;
          if (error.response?.status === 401) {
            setUser(null);
            setMessage("Your session expired. Please sign in again.");
            return;
          }

          failures.push(`${file.name}${detail ? ` - ${detail}` : ""}`);
        }
      }

      setMessage(
        failures.length
          ? `${successes.length} uploaded, ${failures.length} failed. ${failures.join("; ")}`
          : `${successes.length} resume${successes.length === 1 ? "" : "s"} uploaded successfully.`,
      );
      setFiles([]);
      if (uploadInputRef.current) {
        uploadInputRef.current.value = "";
      }
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

  const selectUploadFiles = (selectedFiles) => {
    const nextFiles = Array.from(selectedFiles ?? []).filter(Boolean);

    if (nextFiles.length === 0) {
      return;
    }

    const unsupportedFiles = nextFiles.filter((file) => !isSupportedResumeFile(file));
    const supportedFiles = nextFiles.filter((file) => isSupportedResumeFile(file));
    let nextMessage = "";

    if (unsupportedFiles.length > 0) {
      nextMessage = `Skipped ${unsupportedFiles.length} unsupported file${unsupportedFiles.length === 1 ? "" : "s"}. Please use PDF, DOC, or DOCX.`;
    }

    if (supportedFiles.length > 0) {
      setFiles((currentFiles) => [...currentFiles, ...supportedFiles]);
      nextMessage =
        nextMessage ||
        `Added ${supportedFiles.length} resume${supportedFiles.length === 1 ? "" : "s"} to the upload queue.`;
    }

    setMessage(nextMessage);
  };

  const handleUploadZoneDrop = (event) => {
    event.preventDefault();
    setIsDraggingUpload(false);

    selectUploadFiles(event.dataTransfer.files);
  };

  const handleUploadZoneBrowse = (event) => {
    selectUploadFiles(event.target.files);
    event.target.value = "";
  };

  const clearUploadSelection = () => {
    setFiles([]);
    setMessage("");
    if (uploadInputRef.current) {
      uploadInputRef.current.value = "";
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

  const renderUploadPage = () => (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <p className="section-kicker">Ingestion</p>
          <h1>Upload Resume</h1>
          <p className="subtitle">Add a new parsed candidate to the shared recruiting workspace.</p>
        </div>
      </header>

      <section className={`upload-surface ${isDraggingUpload ? "is-dragging" : ""}`}>
        <label
          className="upload-dropzone"
          onDragEnter={() => setIsDraggingUpload(true)}
          onDragLeave={(event) => {
            if (event.currentTarget === event.target) {
              setIsDraggingUpload(false);
            }
          }}
          onDragOver={(event) => {
            event.preventDefault();
            setIsDraggingUpload(true);
          }}
          onDrop={handleUploadZoneDrop}
        >
          <input
            ref={uploadInputRef}
            className="upload-input"
            type="file"
            multiple
            accept=".pdf,.doc,.docx"
            onChange={handleUploadZoneBrowse}
          />

          <div className="upload-dropzone-copy">
            <div className="upload-badge" aria-hidden="true">
              <UploadIcon />
            </div>
            <div>
              <h2>Drag and drop a resume here</h2>
              <p className="subtitle">
                Drop a PDF, DOC, or DOCX file, or click anywhere in this area to browse.
              </p>
            </div>
          </div>

          <div className="upload-dropzone-meta">
            <span>Accepted files</span>
            <strong>PDF, DOC, DOCX</strong>
          </div>
        </label>

        <div className="upload-actions">
          <div className="upload-file-pill">
            <span>Selected files</span>
            <strong>{files.length ? `${files.length} file${files.length === 1 ? "" : "s"} selected` : "No files selected"}</strong>
          </div>

          {files.length > 0 && (
            <div className="selected-file-list" aria-label="Selected resume files">
              {files.map((file) => (
                <span key={`${file.name}-${file.lastModified}`} title={file.name}>
                  {file.name}
                </span>
              ))}
            </div>
          )}

          <div className="upload-action-buttons">
            <button className="secondary-button" type="button" onClick={clearUploadSelection} disabled={!files.length && !message}>
              Clear
            </button>
            <button className="primary-button" onClick={handleUpload} disabled={isUploading || files.length === 0}>
              {isUploading ? "Uploading..." : "Upload Resume"}
            </button>
          </div>

          <p className="status-message">
            {message || "The file will be uploaded with the existing backend endpoint."}
          </p>
        </div>
      </section>
    </section>
  );

  const renderCandidatesPage = () => (
    <section className="page-stack candidates-stack">
      <header className="page-header candidates-header">
        <div>
          <p className="section-kicker">Candidate pipeline</p>
          <h1>Candidates</h1>
          <p className="subtitle">View and manage all parsed candidate resumes.</p>
        </div>

        <div className="refresh-panel">
          <span className="refresh-timestamp">{formatRefreshTimestamp(lastCandidatesRefresh)}</span>
          <button className="refresh-button" type="button" onClick={loadCandidates} disabled={isLoadingCandidates}>
            <RefreshIcon />
            <span>{isLoadingCandidates ? "Refreshing..." : "Refresh"}</span>
          </button>
        </div>
      </header>

      <div className="candidate-toolbar">
        <label className="search-field search-field--full">
          <span>Search candidates</span>
          <div className="search-input-wrap">
            <SearchIcon />
            <input
              type="search"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder="Search candidates, roles, skills, or companies..."
            />
          </div>
        </label>

        <div className="candidate-count">
          <strong>{visibleCandidates.length}</strong>
          <span>matching records</span>
        </div>
      </div>

      {candidateError && <p className="error-message">{candidateError}</p>}
      {candidateStatus && <p className="success-message">{candidateStatus}</p>}

      <div className="table-shell candidate-table-shell">
        <table className="data-table candidate-table">
          <colgroup>
            <col className="col-id" />
            <col className="col-candidate" />
            <col className="col-email" />
            <col className="col-phone" />
            <col className="col-role" />
            <col className="col-company" />
            <col className="col-experience" />
            <col className="col-career" />
            <col className="col-skills" />
            <col className="col-summary" />
            <col className="col-filename" />
            <col className="col-created" />
            <col className="col-actions" />
          </colgroup>
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
              <th className="actions-column">Actions</th>
            </tr>
          </thead>
          <tbody>
            {candidates.length === 0 ? (
              <tr>
                <td className="empty-state" colSpan="13">
                  {isLoadingCandidates ? "Loading candidates..." : "No candidates saved yet."}
                </td>
              </tr>
            ) : paginatedCandidates.length === 0 ? (
              <tr>
                <td className="empty-state" colSpan="13">
                  No matching candidates found.
                </td>
              </tr>
            ) : (
              paginatedCandidates.map((candidate) => (
                <tr key={candidate.id}>
                  <td>{candidate.id}</td>
                  <td>
                    <TableValue value={candidate.candidate} />
                  </td>
                  <td>
                    <TableValue value={candidate.email} />
                  </td>
                  <td>
                    <TableValue value={candidate.phone} />
                  </td>
                  <td>
                    <TableValue value={candidate.current_position} />
                  </td>
                  <td>
                    <TableValue value={candidate.current_company} />
                  </td>
                  <td>{formatYears(candidate.total_experience_years)}</td>
                  <td>{formatYears(candidate.career_span_years)}</td>
                  <td>
                    <TableValue value={candidate.normalized_skills || candidate.skills} />
                  </td>
                  <td>
                    <TableValue value={candidate.resume_summary} />
                  </td>
                  <td>
                    <TableValue value={candidate.filename} />
                  </td>
                  <td>
                    <TableValue value={candidate.created_at} />
                  </td>
                  <td className="actions-column">
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

      <div className="table-footer">
        <div className="table-summary">
          {visibleCandidates.length === 0
            ? "Showing 0 candidates"
            : `Showing ${candidateStart}-${candidateEnd} of ${visibleCandidates.length} candidates`}
        </div>

        <div className="pagination-controls">
          <button
            className="pagination-button"
            type="button"
            onClick={() => setCurrentCandidatePage((page) => Math.max(page - 1, 1))}
            disabled={currentCandidatePage === 1}
            aria-label="Previous page"
          >
            <ChevronLeftIcon />
          </button>
          <span className="pagination-status">
            Page {currentCandidatePage} of {totalCandidatePages}
          </span>
          <button
            className="pagination-button"
            type="button"
            onClick={() => setCurrentCandidatePage((page) => Math.min(page + 1, totalCandidatePages))}
            disabled={currentCandidatePage === totalCandidatePages}
            aria-label="Next page"
          >
            <ChevronRightIcon />
          </button>
        </div>
      </div>
    </section>
  );

  const renderAuditLogsPage = () => (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <p className="section-kicker">System activity</p>
          <h1>Audit Logs</h1>
          <p className="subtitle">Search and review administrative actions across the platform.</p>
        </div>
      </header>

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

      <div className="table-shell">
        <table className="data-table audit-table">
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
                  <td>
                    <TableValue value={log.timestamp} />
                  </td>
                  <td>
                    <TableValue value={log.user_email} />
                  </td>
                  <td>
                    <TableValue value={log.action} />
                  </td>
                  <td>
                    <TableValue value={formatAuditDetails(log.details)} />
                  </td>
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
  );

  const renderAnalyticsPage = () => (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <p className="section-kicker">Reporting</p>
          <h1>Analytics</h1>
          <p className="subtitle">{isLoadingAnalytics ? "Refreshing..." : "Live database summary."}</p>
        </div>
      </header>

      {analyticsError && <p className="error-message">{analyticsError}</p>}

      <div className="metric-grid">
        {analyticsCards.map((card) => (
          <MetricCard key={card.label} {...card} />
        ))}
      </div>

      <div className="analytics-grid">
        <section className="surface-block">
          <div className="surface-header">
            <div>
              <h2>Top Skills</h2>
              <p className="subtitle">Top 10 normalized skills.</p>
            </div>
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

        <section className="surface-block">
          <div className="surface-header">
            <div>
              <h2>Top Current Roles</h2>
              <p className="subtitle">Most common roles in the current dataset.</p>
            </div>
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

      <section className="surface-block">
        <div className="surface-header">
          <div>
            <h2>Recent Uploads</h2>
            <p className="subtitle">Latest candidate records written to the database.</p>
          </div>
        </div>

        <div className="table-shell compact-table-shell">
          <table className="data-table recent-table">
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
                    <td>
                      <TableValue value={candidate.candidate} />
                    </td>
                    <td>
                      <TableValue value={candidate.email} />
                    </td>
                    <td>
                      <TableValue value={candidate.current_position} />
                    </td>
                    <td>
                      <TableValue value={candidate.filename} />
                    </td>
                    <td>
                      <TableValue value={candidate.created_at} />
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );

  const openUserModal = (mode, user = null) => {
    setSelectedUser(null);
    setUserActionMessage("");
    setUserModalMode(mode);
    setUserModalUser(user);
    setUserModalForm({
      name: user?.name ?? "",
      username: user?.username ?? "",
      email: user?.email ?? "",
      role: user?.role ?? "recruiter",
      password: "",
      confirmPassword: "",
    });
  };

  const closeUserModal = () => {
    setUserModalMode(null);
    setUserModalUser(null);
    setUserActionMessage("");
    setUserModalForm({
      name: "",
      username: "",
      email: "",
      role: "recruiter",
      password: "",
      confirmPassword: "",
    });
  };

  const handleUserModalSubmit = async (event) => {
    event.preventDefault();
    setUserActionMessage("");

    try {
      if (userModalMode === "create") {
        if (userModalForm.password !== userModalForm.confirmPassword) {
          setUserActionMessage("Passwords do not match.");
          return;
        }

        await api.post("/users", {
          name: userModalForm.name,
          username: userModalForm.username,
          email: userModalForm.email,
          password: userModalForm.password,
          role: userModalForm.role,
        });
      } else if (userModalMode === "edit" && userModalUser) {
        await api.patch(`/users/${userModalUser.id}`, {
          name: userModalForm.name,
          username: userModalForm.username,
          email: userModalForm.email,
          role: userModalForm.role,
        });
      } else if (userModalMode === "reset" && userModalUser) {
        if (userModalForm.password !== userModalForm.confirmPassword) {
          setUserActionMessage("Passwords do not match.");
          return;
        }

        await api.post(`/users/${userModalUser.id}/reset-password`, {
          password: userModalForm.password,
        });
      }

      await loadUsers();
      if (userModalMode === "edit" && userModalUser?.id === user?.id) {
        await refreshCurrentUser();
      }
      closeUserModal();
    } catch (error) {
      console.error(error);
      const detail = error.response?.data?.detail;
      setUserActionMessage(detail || "Could not save user changes.");
    }
  };

  const handleToggleLock = async (targetUser) => {
    const shouldLock = !targetUser.is_locked;
    const confirmed = window.confirm(
      `${shouldLock ? "Lock" : "Unlock"} ${targetUser.username}?`,
    );
    if (!confirmed) {
      return;
    }

    try {
      await api.post(`/users/${targetUser.id}/${shouldLock ? "lock" : "unlock"}`);
      await loadUsers();
    } catch (error) {
      console.error(error);
      const detail = error.response?.data?.detail;
      setUsersError(detail || `Could not ${shouldLock ? "lock" : "unlock"} account.`);
    }
  };

  const handleDeleteUser = async (targetUser) => {
    const confirmed = window.confirm(`Delete ${targetUser.username}? This cannot be undone.`);
    if (!confirmed) {
      return;
    }

    try {
      await api.delete(`/users/${targetUser.id}`);
      if (selectedUser?.id === targetUser.id) {
        setSelectedUser(null);
      }
      await loadUsers();
    } catch (error) {
      console.error(error);
      const detail = error.response?.data?.detail;
      setUsersError(detail || "Could not delete user.");
    }
  };

  const openResetPassword = (targetUser) => {
    const confirmed = window.confirm(`Reset password for ${targetUser.username}?`);
    if (!confirmed) {
      return;
    }

    openUserModal("reset", targetUser);
  };

  const renderUsersPage = () => {
    const usersMetrics = [
      { label: "Total Users", value: formatMetric(users.length) },
      { label: "Admin Users", value: formatMetric(users.filter((item) => item.role === "admin").length) },
      { label: "Recruiter Users", value: formatMetric(users.filter((item) => item.role === "recruiter").length) },
      { label: "Locked Accounts", value: formatMetric(users.filter((item) => item.is_locked).length) },
    ];

    return (
      <section className="page-stack">
        <header className="page-header candidates-header">
          <div>
            <p className="section-kicker">Administration</p>
            <h1>User Management</h1>
            <p className="subtitle">Create, update, lock, reset, and remove application users.</p>
          </div>

          <div className="refresh-panel">
            <span className="refresh-timestamp">{formatRefreshTimestamp(lastUsersRefresh)}</span>
            <button className="refresh-button" type="button" onClick={loadUsers} disabled={isLoadingUsers}>
              <RefreshIcon />
              <span>{isLoadingUsers ? "Refreshing..." : "Refresh"}</span>
            </button>
          </div>
        </header>

        {usersError && <p className="error-message">{usersError}</p>}

        <div className="metric-grid">
          {usersMetrics.map((card) => (
            <MetricCard key={card.label} {...card} />
          ))}
        </div>

        <div className="users-toolbar">
          <label className="search-field search-field--full">
            <span>Search users</span>
            <div className="search-input-wrap">
              <SearchIcon />
              <input
                type="search"
                value={userSearchQuery}
                onChange={(event) => setUserSearchQuery(event.target.value)}
                placeholder="Search by name, username, or email..."
              />
            </div>
          </label>

          <button className="primary-button add-user-button" type="button" onClick={() => openUserModal("create")}>
            <UserPlusIcon />
            <span>Add User</span>
          </button>
        </div>

        <div className="table-shell users-table-shell">
          <table className="data-table users-table">
            <colgroup>
              <col className="col-user-avatar" />
              <col className="col-user-name" />
              <col className="col-user-username" />
              <col className="col-user-email" />
              <col className="col-user-role" />
              <col className="col-user-status" />
              <col className="col-user-created" />
              <col className="col-user-login" />
              <col className="col-user-actions" />
            </colgroup>
            <thead>
              <tr>
                <th></th>
                <th>Name</th>
                <th>Username</th>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Date Created</th>
                <th>Last Login</th>
                <th className="actions-column">Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 ? (
                <tr>
                  <td className="empty-state" colSpan="9">
                    {isLoadingUsers ? "Loading users..." : "No users found."}
                  </td>
                </tr>
              ) : paginatedUsers.length === 0 ? (
                <tr>
                  <td className="empty-state" colSpan="9">
                    No matching users found.
                  </td>
                </tr>
              ) : (
                paginatedUsers.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <div className="user-avatar" aria-hidden="true">
                        {String(item.name || item.username || item.email || "U")
                          .trim()
                          .slice(0, 2)
                          .toUpperCase()}
                      </div>
                    </td>
                    <td>
                      <TableValue value={item.name || item.username || item.email} />
                    </td>
                    <td>
                      <TableValue value={item.username} />
                    </td>
                    <td>
                      <TableValue value={item.email} />
                    </td>
                    <td>
                      <label className="inline-select-wrap" aria-label={`Change role for ${item.username}`}>
                        <select
                          className={`role-select role-${item.role || "recruiter"}`}
                          value={item.role || "recruiter"}
                          onChange={(event) => handleUserRoleChange(item, event.target.value)}
                        >
                          <option value="admin">Admin</option>
                          <option value="recruiter">Recruiter</option>
                        </select>
                      </label>
                    </td>
                    <td>
                      <span className={`status-pill ${item.is_locked ? "status-failed" : "status-success"}`}>
                        {item.is_locked ? "Locked" : "Active"}
                      </span>
                    </td>
                    <td>
                      <TableValue value={item.created_at} />
                    </td>
                    <td>
                      <TableValue value={item.last_login || "Never"} />
                    </td>
                    <td className="actions-column">
                      <div className="row-actions">
                        <button
                          className="icon-button icon-button--key"
                          type="button"
                          onClick={() => openResetPassword(item)}
                          title="Reset Password"
                          aria-label={`Reset password for ${item.username}`}
                        >
                          <KeyIcon />
                        </button>
                        <button
                          className={`icon-button ${item.is_locked ? "icon-button--unlock" : "icon-button--lock"}`}
                          type="button"
                          onClick={() => handleToggleLock(item)}
                          title={item.is_locked ? "Unlock Account" : "Lock Account"}
                          aria-label={`${item.is_locked ? "Unlock" : "Lock"} account for ${item.username}`}
                        >
                          {item.is_locked ? <UnlockIcon /> : <LockIcon />}
                        </button>
                        <button
                          className="icon-button icon-button--danger"
                          type="button"
                          onClick={() => handleDeleteUser(item)}
                          title="Delete User"
                          aria-label={`Delete user ${item.username}`}
                        >
                          <TrashIcon />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="table-footer users-footer">
          <div className="table-summary">
            {visibleUsers.length === 0
              ? "Showing 0 users"
              : `Showing ${userStart}-${userEnd} of ${visibleUsers.length} users`}
          </div>

          <div className="pagination-controls">
            <button
              className="pagination-button"
              type="button"
              onClick={() => setCurrentUsersPage((page) => Math.max(page - 1, 1))}
              disabled={currentUsersPage === 1}
              aria-label="Previous page"
            >
              <ChevronLeftIcon />
            </button>
            <span className="pagination-status">
              Page {currentUsersPage} of {totalUserPages}
            </span>
            <button
              className="pagination-button"
              type="button"
              onClick={() => setCurrentUsersPage((page) => Math.min(page + 1, totalUserPages))}
              disabled={currentUsersPage === totalUserPages}
              aria-label="Next page"
            >
              <ChevronRightIcon />
            </button>
          </div>
        </div>

        <section className="legend-bar">
          <h2>Actions Legend</h2>
          <div className="legend-items">
            <div className="legend-item">
              <span className="icon-button icon-button--key" aria-hidden="true">
                <KeyIcon />
              </span>
              <span>Reset Password</span>
            </div>
            <div className="legend-item">
              <span className="icon-button icon-button--lock" aria-hidden="true">
                <LockIcon />
              </span>
              <span>Lock Account</span>
            </div>
            <div className="legend-item">
              <span className="icon-button icon-button--unlock" aria-hidden="true">
                <UnlockIcon />
              </span>
              <span>Unlock Account</span>
            </div>
            <div className="legend-item">
              <span className="icon-button icon-button--danger" aria-hidden="true">
                <TrashIcon />
              </span>
              <span>Delete User</span>
            </div>
          </div>
        </section>
      </section>
    );
  };

  const renderSecurityDashboardPage = () => {
    const summary = securityDashboard.summary || emptySecurityDashboard.summary;
    const securityCards = [
      { label: "Total Users", value: formatMetric(summary.total_users) },
      { label: "Admin Users", value: formatMetric(summary.admin_users) },
      { label: "Recruiter Users", value: formatMetric(summary.recruiter_users) },
      { label: "Locked Accounts", value: formatMetric(summary.locked_accounts) },
      { label: "Total Candidates", value: formatMetric(summary.total_candidates) },
      { label: "Total Resume Uploads", value: formatMetric(summary.total_resume_uploads) },
      { label: "Audit Events Today", value: formatMetric(summary.audit_events_today) },
      { label: "Failed Login Attempts", value: formatMetric(summary.failed_login_attempts) },
    ];

    return (
      <section className="page-stack">
        <header className="page-header">
          <div>
            <p className="section-kicker">Administration</p>
            <h1>Security Dashboard</h1>
            <p className="subtitle">Track access, account status, and the latest security-relevant activity.</p>
          </div>

          <div className="refresh-panel">
            <span className="refresh-timestamp">{formatRefreshTimestamp(lastSecurityRefresh)}</span>
            <button
              className="refresh-button"
              type="button"
              onClick={async () => {
                const [dashboardLoaded, logsLoaded] = await Promise.all([
                  loadSecurityDashboard(),
                  loadAuditLogs(),
                ]);
                if (dashboardLoaded || logsLoaded) {
                  setLastSecurityRefresh(new Date());
                }
              }}
              disabled={isLoadingSecurityDashboard || isLoadingAuditLogs}
            >
              <RefreshIcon />
              <span>{isLoadingSecurityDashboard || isLoadingAuditLogs ? "Refreshing..." : "Refresh"}</span>
            </button>
          </div>
        </header>

        {securityDashboardError && <p className="error-message">{securityDashboardError}</p>}
        {auditLogError && <p className="error-message">{auditLogError}</p>}

        <div className="metric-grid">
          {securityCards.map((card) => (
            <MetricCard key={card.label} {...card} />
          ))}
        </div>

        <section className="surface-block">
          <div className="surface-header">
            <div>
              <h2>Recent Activity</h2>
              <p className="subtitle">Events sourced from the existing audit log.</p>
            </div>
          </div>

          <div className="table-shell">
            <table className="data-table security-table">
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
                {securityDashboard.recent_activity.length === 0 ? (
                  <tr>
                    <td className="empty-state" colSpan="5">
                      No security activity recorded yet.
                    </td>
                  </tr>
                ) : (
                  securityDashboard.recent_activity.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <TableValue value={item.timestamp} />
                      </td>
                      <td>
                        <TableValue value={item.user_email} />
                      </td>
                      <td>
                        <TableValue value={item.action} />
                      </td>
                      <td>
                        <TableValue value={formatAuditDetails(item.details)} />
                      </td>
                      <td>
                        <span className={`status-pill status-${getAuditStatus(item.status)}`}>
                          {String(item.status || "").toUpperCase()}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="surface-block">
          <div className="surface-header">
            <div>
              <h2>Audit Logs</h2>
              <p className="subtitle">
                Search, filter, and review the full audit trail without leaving Security Dashboard.
              </p>
            </div>
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

          <div className="table-shell">
            <table className="data-table audit-table">
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
                      <td>
                        <TableValue value={log.timestamp} />
                      </td>
                      <td>
                        <TableValue value={log.user_email} />
                      </td>
                      <td>
                        <TableValue value={log.action} />
                      </td>
                      <td>
                        <TableValue value={formatAuditDetails(log.details)} />
                      </td>
                      <td>
                        <span className={`status-pill status-${getAuditStatus(log.status)}`}>
                          {String(log.status || "").toUpperCase()}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      </section>
    );
  };

  const renderPlaceholderPage = (title, subtitle) => (
    <section className="page-stack">
      <header className="page-header">
        <div>
          <p className="section-kicker">Workspace</p>
          <h1>{title}</h1>
          <p className="subtitle">{subtitle}</p>
        </div>
      </header>

      <section className="surface-block placeholder-block">
        <h2>{title} is ready for your next integration.</h2>
        <p className="subtitle">
          This view is intentionally lightweight for now so the navigation stays intact without
          changing backend behavior.
        </p>
      </section>
    </section>
  );

  if (isCheckingAuth) {
    return (
      <main className="auth-page">
        <section className="auth-panel">
          <img className="auth-logo" src="/atlantic-group-logo.svg" alt="The Atlantic Group" />
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
            <img className="auth-logo" src="/atlantic-group-logo.svg" alt="The Atlantic Group" />
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

  const pageContent = {
    candidates: renderCandidatesPage(),
    upload: renderUploadPage(),
    analytics: renderAnalyticsPage(),
    jobBoard: renderPlaceholderPage(
      "Job Board",
      "Track live requisitions, open headcount, and placement priorities.",
    ),
    securityDashboard: renderSecurityDashboardPage(),
    users: renderUsersPage(),
  };

  const userInitial = user.email ? user.email.slice(0, 1).toUpperCase() : "A";

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <button className="sidebar-brand" type="button" onClick={() => setActivePage("candidates")}>
          <img src="/atlantic-group-logo.svg" alt="The Atlantic Group" />
        </button>

        <nav className="sidebar-nav" aria-label="Primary">
          {navigationItems.map((item) => {
            const Icon = item.icon;
            const isActive = safeActivePage === item.key;

            return (
              <button
                key={item.key}
                type="button"
                className={`sidebar-link ${isActive ? "is-active" : ""}`}
                onClick={() => setActivePage(item.key)}
                aria-current={isActive ? "page" : undefined}
              >
                <Icon />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-user">
            <div className="sidebar-avatar" aria-hidden="true">
              {userInitial}
            </div>
            <div className="sidebar-user-copy">
              <strong>{formatValue(user.email)}</strong>
              <span>{formatValue(user.role)}</span>
            </div>
          </div>

          <button className="sidebar-signout" type="button" onClick={handleLogout}>
            Sign Out
          </button>
        </div>
      </aside>

      <section className="app-content">{pageContent[safeActivePage] ?? pageContent.candidates}</section>

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

      {selectedUser && (
        <div className="modal-overlay" onClick={() => setSelectedUser(null)}>
          <section
            className="details-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="user-details-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="modal-header">
              <div>
                <h2 id="user-details-title">User Details</h2>
                <p>{formatValue(selectedUser.name || selectedUser.username)}</p>
              </div>

              <button className="modal-close" type="button" onClick={() => setSelectedUser(null)}>
                Close
              </button>
            </header>

            <div className="details-grid">
              <section className="detail-card">
                <h3>Account</h3>
                <dl>
                  <DetailItem label="Name" value={selectedUser.name} />
                  <DetailItem label="Username" value={selectedUser.username} />
                  <DetailItem label="Email" value={selectedUser.email} />
                </dl>
              </section>

              <section className="detail-card">
                <h3>Access</h3>
                <dl>
                  <DetailItem label="Role" value={selectedUser.role} />
                  <DetailItem label="Status" value={selectedUser.is_locked ? "Locked" : "Active"} />
                  <DetailItem label="Created" value={selectedUser.created_at} />
                  <DetailItem label="Last Login" value={selectedUser.last_login || "Never"} />
                </dl>
              </section>
            </div>
          </section>
        </div>
      )}

      {userModalMode && (
        <div className="modal-overlay" onClick={closeUserModal}>
          <section
            className="details-modal user-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="user-modal-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="modal-header">
              <div>
                <h2 id="user-modal-title">
                  {userModalMode === "create"
                    ? "Create User"
                    : userModalMode === "edit"
                      ? "Edit User"
                      : "Reset Password"}
                </h2>
                <p>
                  {userModalMode === "create"
                    ? "Create a new admin or recruiter account."
                    : userModalMode === "edit"
                      ? "Update account details and role."
                      : `Reset the password for ${userModalUser?.username || "this user"}.`}
                </p>
              </div>

              <button className="modal-close" type="button" onClick={closeUserModal}>
                Close
              </button>
            </header>

            <form className="user-form" onSubmit={handleUserModalSubmit}>
              {userActionMessage && <p className="error-message">{userActionMessage}</p>}

              {userModalMode !== "reset" ? (
                <div className="user-form-grid">
                  <label className="audit-filter">
                    <span>Name</span>
                    <input
                      type="text"
                      value={userModalForm.name}
                      onChange={(event) =>
                        setUserModalForm((currentForm) => ({ ...currentForm, name: event.target.value }))
                      }
                      required
                    />
                  </label>

                  <label className="audit-filter">
                    <span>Username</span>
                    <input
                      type="text"
                      value={userModalForm.username}
                      onChange={(event) =>
                        setUserModalForm((currentForm) => ({
                          ...currentForm,
                          username: event.target.value,
                        }))
                      }
                      required
                    />
                  </label>

                  <label className="audit-filter">
                    <span>Email</span>
                    <input
                      type="email"
                      value={userModalForm.email}
                      onChange={(event) =>
                        setUserModalForm((currentForm) => ({ ...currentForm, email: event.target.value }))
                      }
                      required
                    />
                  </label>

                  <label className="audit-filter">
                    <span>Role</span>
                    <select
                      value={userModalForm.role}
                      onChange={(event) =>
                        setUserModalForm((currentForm) => ({ ...currentForm, role: event.target.value }))
                      }
                    >
                      <option value="admin">Admin</option>
                      <option value="recruiter">Recruiter</option>
                    </select>
                  </label>
                </div>
              ) : null}

              {userModalMode === "create" && (
                <div className="user-form-grid">
                  <label className="audit-filter">
                    <span>Password</span>
                    <input
                      type="password"
                      value={userModalForm.password}
                      onChange={(event) =>
                        setUserModalForm((currentForm) => ({
                          ...currentForm,
                          password: event.target.value,
                        }))
                      }
                      required
                    />
                  </label>

                  <label className="audit-filter">
                    <span>Confirm Password</span>
                    <input
                      type="password"
                      value={userModalForm.confirmPassword}
                      onChange={(event) =>
                        setUserModalForm((currentForm) => ({
                          ...currentForm,
                          confirmPassword: event.target.value,
                        }))
                      }
                      required
                    />
                  </label>
                </div>
              )}

              {userModalMode === "reset" && (
                <div className="user-form-grid">
                  <label className="audit-filter">
                    <span>New Password</span>
                    <input
                      type="password"
                      value={userModalForm.password}
                      onChange={(event) =>
                        setUserModalForm((currentForm) => ({
                          ...currentForm,
                          password: event.target.value,
                        }))
                      }
                      required
                    />
                  </label>

                  <label className="audit-filter">
                    <span>Confirm Password</span>
                    <input
                      type="password"
                      value={userModalForm.confirmPassword}
                      onChange={(event) =>
                        setUserModalForm((currentForm) => ({
                          ...currentForm,
                          confirmPassword: event.target.value,
                        }))
                      }
                      required
                    />
                  </label>
                </div>
              )}

              <div className="modal-actions">
                <button className="secondary-button" type="button" onClick={closeUserModal}>
                  Cancel
                </button>
                <button className="primary-button" type="submit">
                  {userModalMode === "create"
                    ? "Create User"
                    : userModalMode === "edit"
                      ? "Save Changes"
                      : "Reset Password"}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </main>
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

export default App;
