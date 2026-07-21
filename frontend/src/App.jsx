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
  "Job Created",
  "Job Updated",
  "Job Deleted",
  "Job View",
  "Job Matches View",
];

const auditStatusOptions = ["All Statuses", "Success", "Failed", "Denied", "Warning"];

const jobStatusOptions = ["open", "paused", "closed", "draft", "offer sent"];
const jobTypeOptions = ["Full-time", "Contract", "Contract-to-hire", "Part-time", "Temporary"];

const emptyJobBoard = {
  jobs: [],
  summary: {
    total_jobs: 0,
    open_jobs: 0,
    active_candidates: 0,
    strong_matches: 0,
    offers_sent: 0,
    average_time_to_fill: "N/A",
  },
};

const emptyJobForm = {
  title: "",
  department: "",
  location: "",
  job_type: "Full-time",
  status: "open",
  description: "",
  required_skills: "",
  salary: "",
};

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

function formatDetailValue(value, fallback) {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }

  const text = String(value);
  if (!text.trim() || missingValueTokens.has(text.trim().toLowerCase())) {
    return fallback;
  }

  return text;
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

function formatLabel(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase()) || "Not found";
}

const normalizeSource = (value) => String(value ?? "").trim().toLowerCase();

function inferSourceFromUrl(url) {
  const hostname = String(url || "")
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .split("/")[0];

  if (hostname.includes("atlanticrecruiters.com")) {
    return "Atlantic Group";
  }

  if (hostname.includes("blairandpotts.com")) {
    return "Blair & Potts";
  }

  return "";
}

function formatPercent(value) {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) {
    return "0%";
  }

  return `${Math.round(numberValue)}%`;
}

function formatFileSize(size) {
  const sizeValue = Number(size);
  if (!Number.isFinite(sizeValue) || sizeValue <= 0) {
    return "0 KB";
  }

  if (sizeValue < 1024 * 1024) {
    return `${Math.max(1, Math.round(sizeValue / 1024))} KB`;
  }

  return `${(sizeValue / (1024 * 1024)).toFixed(1)} MB`;
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

function formatDate(value) {
  if (!value) {
    return "N/A";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "N/A";
  }

  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatSalary(value) {
  const text = String(value ?? "").trim();
  return text ? text : "N/A";
}

function cleanJobText(value) {
  return String(value ?? "")
    .replace(/\r\n/g, "\n")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/(?:Your Name|Email Address|Phone Number|Upload Resume|Attach a resume file|Footer|Submit Application)\b[\s\S]*$/i, "")
    .trim();
}

function splitJobDescription(description) {
  const cleanedDescription = cleanJobText(description);
  if (!cleanedDescription) {
    return {
      overview: [],
      responsibilities: [],
      qualifications: [],
      benefits: [],
      additionalNotes: [],
      applicationNotice: [],
    };
  }

  const sectionMatchers = [
    { key: "overview", label: "Job Overview" },
    { key: "responsibilities", label: "Responsibilities" },
    { key: "qualifications", label: "Qualifications" },
    { key: "benefits", label: "Benefits" },
    { key: "additionalNotes", label: "Additional Notes" },
    { key: "applicationNotice", label: "Application Notice" },
  ];

  const normalizedText = cleanedDescription.replace(/\r\n/g, "\n");
  const sectionMap = sectionMatchers.reduce((accumulator, section) => {
    accumulator[section.key] = [];
    return accumulator;
  }, {});

  const headingPatterns = [
    { key: "overview", pattern: /job overview\s*[-:–—]?\s*/gi },
    { key: "responsibilities", pattern: /responsibilities(?:\s+as)?\s*(?:the|a|an)?[^:\n]*:\s*/gi },
    { key: "qualifications", pattern: /qualifications(?:\s+for)?\s*(?:the|a|an)?[^:\n]*:\s*/gi },
    { key: "benefits", pattern: /benefits(?:\s+and\s+perks)?\s*:\s*/gi },
    { key: "additionalNotes", pattern: /(?:additional notes|notes|other information)\s*:\s*/gi },
    { key: "applicationNotice", pattern: /application notice\s*:\s*/gi },
  ];

  const matches = headingPatterns
    .flatMap((section) => {
      const sectionMatches = [];
      let match;
      while ((match = section.pattern.exec(normalizedText)) !== null) {
        sectionMatches.push({
          key: section.key,
          index: match.index,
          length: match[0].length,
        });
      }
      return sectionMatches;
    })
    .sort((firstMatch, secondMatch) => firstMatch.index - secondMatch.index);

  if (matches.length === 0) {
    return {
      overview: [cleanedDescription],
      responsibilities: [],
      qualifications: [],
      benefits: [],
      additionalNotes: [],
      applicationNotice: [],
    };
  }

  matches.forEach((match, index) => {
    const startIndex = match.index + match.length;
    const endIndex = index + 1 < matches.length ? matches[index + 1].index : normalizedText.length;
    const sectionText = cleanJobText(normalizedText.slice(startIndex, endIndex));
    if (sectionText) {
      sectionMap[match.key].push(sectionText);
    }
  });

  return sectionMap;
}

function normalizeSectionValues(value) {
  if (value === null || value === undefined) {
    return [];
  }

  if (Array.isArray(value)) {
    return value.flatMap((entry) => normalizeSectionValues(entry));
  }

  if (typeof value === "string") {
    const text = value.trim();
    if (!text) {
      return [];
    }

    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) {
        return parsed.flatMap((entry) => normalizeSectionValues(entry));
      }
      if (parsed && typeof parsed === "object") {
        return normalizeSectionValues(Object.values(parsed));
      }
      if (typeof parsed === "string") {
        return normalizeSectionValues(parsed);
      }
    } catch {
      // Fall through to plain-text handling.
    }

    const commaSeparated = text
      .split(/\n|,|;\s*/)
      .map((entry) => cleanJobText(entry))
      .filter(Boolean);
    return commaSeparated.length > 1 ? commaSeparated : [text];
  }

  if (typeof value === "object") {
    return Object.values(value).flatMap((entry) => normalizeSectionValues(entry));
  }

  return [String(value).trim()].filter(Boolean);
}

function formatSectionParagraphs(value) {
  return normalizeSectionValues(value)
    .map((entry) => cleanJobText(entry))
    .filter(Boolean);
}

function buildOverviewParagraphs(job) {
  const jobText = cleanJobText(job?.description || job?.full_description || job?.job_description || "");
  if (!jobText) {
    return [];
  }

  const titleText = cleanJobText(job?.title || "");
  const sectionBoundaryPattern = /\b(?:Responsibilities|Qualifications|Benefits|Additional Notes|What You’ll Do|What You'll Do|What You’ll Bring|What You'll Bring|What We Offer|What We Provide|Compensation|Salary)\b/i;
  const titleIndex = titleText ? jobText.toLowerCase().indexOf(titleText.toLowerCase()) : -1;

  let overviewSource = "";
  if (titleIndex >= 0) {
    overviewSource = jobText.slice(titleIndex + titleText.length);
  } else {
    overviewSource = jobText;
  }

  const boundaryMatch = overviewSource.match(sectionBoundaryPattern);
  if (boundaryMatch && typeof boundaryMatch.index === "number") {
    overviewSource = overviewSource.slice(0, boundaryMatch.index);
  }

  const marketingFilters = [
    /about clay/i,
    /our mission/i,
    /we see growth as a creative practice/i,
    /investor/i,
    /funding/i,
    /community/i,
    /culture/i,
    /glassdoor/i,
    /forbes/i,
    /new york times|nyt/i,
    /customers/i,
    /slack/i,
    /club members/i,
    /employee tender offer/i,
    /valuation/i,
    /operating principles/i,
  ];

  const paragraphs = toTextParagraphs(overviewSource)
    .map((paragraph) => paragraph.trim())
    .filter((paragraph) => paragraph && !marketingFilters.some((pattern) => pattern.test(paragraph)))
    .filter((paragraph, index, array) => array.findIndex((value) => value.toLowerCase() === paragraph.toLowerCase()) === index);

  return paragraphs;
}

function getStructuredJobSections(job) {
  const responsibilities = formatSectionParagraphs(job?.responsibilities);
  const qualifications = formatSectionParagraphs(job?.qualifications);
  const benefits = formatSectionParagraphs(job?.benefits);
  const additionalNotes = formatSectionParagraphs(job?.additional_notes);
  const experience = extractExperienceRequirements(job, { responsibilities, qualifications, benefits, additionalNotes });

  const fallbackSections = splitJobDescription(job?.description || job?.full_description || job?.job_description || "");

  return {
    overview: buildOverviewParagraphs(job),
    responsibilities: responsibilities.length > 0 ? responsibilities : fallbackSections.responsibilities,
    qualifications: qualifications.length > 0 ? qualifications : fallbackSections.qualifications,
    experienceRequired: experience.items,
    experienceYears: experience.years,
    benefits: benefits.length > 0 ? benefits : fallbackSections.benefits,
    additionalNotes: additionalNotes.length > 0 ? additionalNotes : fallbackSections.additionalNotes,
  };
}

function extractExperienceRequirements(job, sections = {}) {
  const sources = [
    ...(sections.qualifications || []),
    ...(sections.responsibilities || []),
    ...(sections.additionalNotes || []),
    cleanJobText(job?.description || job?.full_description || job?.job_description || ""),
  ].filter(Boolean);

  const experienceItems = [];
  let experienceYears = "";
  const yearPattern = /\b(\d+(?:\.\d+)?)\s*\+?\s*years?(?:\s+of)?\s+experience\b/i;
  const experienceSignals = [
    /\bexperience\b/i,
    /\bmanaged\b/i,
    /\bmanaging\b/i,
    /\bpublic accounting\b/i,
    /\bb2b\b/i,
    /\bsaas\b/i,
    /\bsql\b/i,
    /\basc\s*740\b/i,
    /\bteams?\b/i,
    /\blead(?:ing|ership)\b/i,
    /\bbackground\b/i,
    /\bworked with\b/i,
    /\bworking with\b/i,
  ];

  for (const source of sources) {
    const text = cleanJobText(source);
    if (!text) {
      continue;
    }

    const segments = text
      .split(/\n|[••]\s*|\.\s+(?=[A-Z0-9])/)
      .map((segment) => cleanJobText(segment))
      .filter(Boolean);

    for (const segment of segments) {
      const lower = segment.toLowerCase();
      const hasExperienceSignal = experienceSignals.some((pattern) => pattern.test(segment));
      if (!hasExperienceSignal) {
        continue;
      }

      if (!experienceItems.some((item) => item.toLowerCase() === lower)) {
        experienceItems.push(segment);
      }

      const yearMatch = segment.match(yearPattern);
      if (yearMatch && !experienceYears) {
        experienceYears = `${yearMatch[1]}+ Years`;
      }
    }
  }

  return { items: experienceItems, years: experienceYears };
}

function toTextParagraphs(value) {
  return cleanJobText(value)
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
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

function SpinnerIcon({ className = "" }) {
  return (
    <svg className={`app-icon spinner-icon ${className}`.trim()} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle className="spinner-track" cx="12" cy="12" r="8.5" />
      <path className="spinner-segment" d="M20.5 12a8.5 8.5 0 0 0-8.5-8.5" />
    </svg>
  );
}

function JobTitleIcon() {
  return (
    <AppIcon>
      <path d="M7 5.5h10" />
      <path d="M7 9.5h10" />
      <path d="M7 13.5h7" />
      <path d="M5.5 4.5h.01" />
      <path d="M5.5 8.5h.01" />
      <path d="M5.5 12.5h.01" />
    </AppIcon>
  );
}

function LocationIcon() {
  return (
    <AppIcon>
      <path d="M12 20s5-4.4 5-9a5 5 0 1 0-10 0c0 4.6 5 9 5 9Z" />
      <path d="M12 11.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z" />
    </AppIcon>
  );
}

function BriefcaseIcon() {
  return (
    <AppIcon>
      <path d="M9 7V6a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 6v1" />
      <path d="M4.5 8.5h15v8a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1v-8Z" />
      <path d="M4.5 11.5h15" />
    </AppIcon>
  );
}

function CurrencyIcon() {
  return (
    <AppIcon>
      <path d="M12 4.5v15" />
      <path d="M15.5 7.2a3.2 3.2 0 0 0-2.4-1.2h-2.6a2.75 2.75 0 1 0 0 5.5h2.6a2.75 2.75 0 1 1 0 5.5h-3.3a3.2 3.2 0 0 1-2.4-1.2" />
    </AppIcon>
  );
}

function CalendarIcon() {
  return (
    <AppIcon>
      <path d="M7 4.5v2" />
      <path d="M17 4.5v2" />
      <path d="M4.5 8h15" />
      <path d="M6 6.5h12A1.5 1.5 0 0 1 19.5 8v10A1.5 1.5 0 0 1 18 19.5H6A1.5 1.5 0 0 1 4.5 18V8A1.5 1.5 0 0 1 6 6.5Z" />
    </AppIcon>
  );
}

function DepartmentIcon() {
  return (
    <AppIcon>
      <path d="M4.5 19.5h15" />
      <path d="M7 19.5V9.5h10v10" />
      <path d="M9 19.5v-4h2v4" />
      <path d="M12.5 4.5h-5v5h5z" />
    </AppIcon>
  );
}

function HashIcon() {
  return (
    <AppIcon>
      <path d="M9 4.5 7.5 19.5" />
      <path d="M16.5 4.5 15 19.5" />
      <path d="M5.5 9h13" />
      <path d="M4.5 15h13" />
    </AppIcon>
  );
}

function PeopleIcon() {
  return (
    <AppIcon>
      <path d="M9.5 11a3 3 0 1 0-3-3 3 3 0 0 0 3 3Z" />
      <path d="M3.5 19.5a6 6 0 0 1 12 0" />
      <path d="M16.5 12.5a2.7 2.7 0 0 0 2.5-2.7 2.8 2.8 0 0 0-2.2-2.7" />
    </AppIcon>
  );
}

function SparkIcon() {
  return (
    <AppIcon>
      <path d="m12 4.5 1.3 4.2 4.2 1.3-4.2 1.3L12 15.5l-1.3-4.2-4.2-1.3 4.2-1.3L12 4.5Z" />
    </AppIcon>
  );
}

function EditIcon() {
  return (
    <AppIcon>
      <path d="m5.5 15 8.9-8.9a1.4 1.4 0 0 1 2 0l1.5 1.5a1.4 1.4 0 0 1 0 2L9 18.1 5 19l.5-4Z" />
      <path d="M14.5 6.5 17.5 9.5" />
    </AppIcon>
  );
}

function ScrapeIcon() {
  return (
    <AppIcon>
      <path d="M7 5.5h10v13H7z" />
      <path d="M9.5 5.5V4a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v1.5" />
      <path d="M9.5 10h5" />
      <path d="M9.5 13h5" />
    </AppIcon>
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

function CheckIcon() {
  return (
    <AppIcon>
      <path d="m6.5 12.4 3.2 3.1 7.8-8" />
    </AppIcon>
  );
}

function FileIcon() {
  return (
    <AppIcon>
      <path d="M7 4.5h7l3 3V19a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V5.5a1 1 0 0 1 1-1Z" />
      <path d="M13.5 4.5V8H17" />
      <path d="M9 12h6" />
      <path d="M9 15.5h4.5" />
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
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadStatus, setUploadStatus] = useState("idle");
  const [uploadError, setUploadError] = useState("");
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
  const [jobBoard, setJobBoard] = useState(emptyJobBoard);
  const [isLoadingJobs, setIsLoadingJobs] = useState(false);
  const [jobBoardError, setJobBoardError] = useState("");
  const [jobFilters, setJobFilters] = useState({
    query: "",
    source: "All Sources",
    department: "All Departments",
    location: "All Locations",
    status: "All Statuses",
    jobType: "All Types",
  });
  const [currentJobPage, setCurrentJobPage] = useState(1);
  const [selectedJob, setSelectedJob] = useState(null);
  const [jobMatches, setJobMatches] = useState([]);
  const [isLoadingJobMatches, setIsLoadingJobMatches] = useState(false);
  const [jobMatchMessage, setJobMatchMessage] = useState("");
  const [jobMatchStatus, setJobMatchStatus] = useState("idle");
  const [jobMatchSummary, setJobMatchSummary] = useState({
    analyzedCount: null,
    highestScore: null,
  });
  const [jobMatchErrorMessage, setJobMatchErrorMessage] = useState("");
  const [jobMatchStageIndex, setJobMatchStageIndex] = useState(0);
  const [jobMatchTab, setJobMatchTab] = useState("top");
  const [selectedMatch, setSelectedMatch] = useState(null);
  const [isJobDetailsModalOpen, setIsJobDetailsModalOpen] = useState(false);
  const [isJobModalOpen, setIsJobModalOpen] = useState(false);
  const [jobForm, setJobForm] = useState(emptyJobForm);
  const [isSavingJob, setIsSavingJob] = useState(false);
  const [jobActionMessage, setJobActionMessage] = useState("");
  const [isEditingJob, setIsEditingJob] = useState(false);
  const [isSavingEditedJob, setIsSavingEditedJob] = useState(false);
  const [jobEditMessage, setJobEditMessage] = useState("");
  const [jobEditError, setJobEditError] = useState("");
  const [jobEditForm, setJobEditForm] = useState({
    title: "",
    location: "",
    department: "",
    employment_type: "",
    salary: "",
    job_number: "",
    description: "",
    active: true,
  });
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
    user: "All Users",
    action: "All Actions",
    status: "All Statuses",
    startDate: "",
    endDate: "",
  });
  const [currentAuditPage, setCurrentAuditPage] = useState(1);
  const [currentCandidatePage, setCurrentCandidatePage] = useState(1);
  const [lastCandidatesRefresh, setLastCandidatesRefresh] = useState(null);
  const [isDraggingUpload, setIsDraggingUpload] = useState(false);
  const uploadInputRef = useRef(null);
  const uploadProgressTimerRef = useRef(null);
  const jobMatchRequestRef = useRef(0);
  const activeJobIdRef = useRef(null);
  const jobMatchInProgressRef = useRef(false);

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

  const stopUploadProgressSimulation = useCallback(() => {
    if (uploadProgressTimerRef.current) {
      window.clearInterval(uploadProgressTimerRef.current);
      uploadProgressTimerRef.current = null;
    }
  }, [isEditingJob, isSavingEditedJob]);

  const beginUploadProgressSimulation = useCallback(() => {
    stopUploadProgressSimulation();
    setUploadStatus("uploading");
    setUploadError("");
    setUploadProgress(6);

    uploadProgressTimerRef.current = window.setInterval(() => {
      setUploadProgress((currentProgress) => {
        if (currentProgress >= 90) {
          return 90;
        }

        const remainingProgress = 90 - currentProgress;
        const nextStep = Math.max(0.5, Math.min(7, remainingProgress * 0.16));
        return Math.min(90, currentProgress + nextStep);
      });
    }, 320);
  }, [stopUploadProgressSimulation]);

  const completeUploadProgress = useCallback(() => {
    stopUploadProgressSimulation();
    setUploadProgress(100);
    setUploadStatus("success");
    setUploadError("");
  }, [stopUploadProgressSimulation]);

  const failUploadProgress = useCallback(
    (errorMessage) => {
      stopUploadProgressSimulation();
      setUploadStatus("error");
      setUploadError(errorMessage);
    },
    [stopUploadProgressSimulation],
  );

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
    const selectedUser = auditFilters.user === "All Users" ? "" : auditFilters.user;
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
        const matchesUser = !selectedUser || log.user_email === selectedUser;
        const matchesAction = !selectedAction || log.action === selectedAction;
        const matchesStatus = !selectedStatus || status === selectedStatus;
        const matchesStartDate = !auditFilters.startDate || logDate >= auditFilters.startDate;
        const matchesEndDate = !auditFilters.endDate || logDate <= auditFilters.endDate;

        return (
          matchesQuery &&
          matchesUser &&
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
    (value) =>
      value && value !== "All Users" && value !== "All Actions" && value !== "All Statuses",
  );

  const auditUserOptions = useMemo(
    () =>
      Array.from(new Set(auditLogs.map((log) => log.user_email).filter(Boolean))).sort((first, second) =>
        String(first).localeCompare(String(second), undefined, {
          sensitivity: "base",
        }),
      ),
    [auditLogs],
  );

  const auditLogsPerPage = 10;
  const totalAuditPages = Math.max(1, Math.ceil(visibleAuditLogs.length / auditLogsPerPage));
  const paginatedAuditLogs = useMemo(() => {
    const startIndex = (currentAuditPage - 1) * auditLogsPerPage;
    return visibleAuditLogs.slice(startIndex, startIndex + auditLogsPerPage);
  }, [currentAuditPage, visibleAuditLogs]);
  const auditStart = visibleAuditLogs.length === 0 ? 0 : (currentAuditPage - 1) * auditLogsPerPage + 1;
  const auditEnd = Math.min(currentAuditPage * auditLogsPerPage, visibleAuditLogs.length);

  const jobs = useMemo(() => jobBoard.jobs || [], [jobBoard.jobs]);
  const visibleJobs = useMemo(() => {
    const normalizedQuery = jobFilters.query.trim().toLowerCase();
    const selectedSource = normalizeSource(jobFilters.source);

    return jobs.filter((job) => {
      const matchesQuery =
        !normalizedQuery ||
        [
          job.title,
          job.job_id,
          job.department,
          job.location,
          job.job_type,
          job.employment_type,
          job.job_number,
          job.salary,
          job.description,
        ].some((value) =>
          String(value ?? "")
            .toLowerCase()
            .includes(normalizedQuery),
        );
      const matchesSource =
        jobFilters.source === "All Sources" ||
        normalizeSource(job.source) === selectedSource;
      const matchesDepartment =
        jobFilters.department === "All Departments" || job.department === jobFilters.department;
      const matchesLocation =
        jobFilters.location === "All Locations" || job.location === jobFilters.location;
      const matchesStatus =
        jobFilters.status === "All Statuses" || job.status === jobFilters.status;
      const matchesJobType =
        jobFilters.jobType === "All Types" || job.job_type === jobFilters.jobType;

      return matchesQuery && matchesSource && matchesDepartment && matchesLocation && matchesStatus && matchesJobType;
    });
  }, [jobFilters, jobs]);

  const jobSources = useMemo(
    () =>
      Array.from(
        new Set(
          jobs
            .map((job) => job.source || job.company || inferSourceFromUrl(job.url) || "Unknown")
            .filter(Boolean),
        ),
      ).sort((firstSource, secondSource) =>
        String(firstSource).localeCompare(String(secondSource), undefined, {
          sensitivity: "base",
        }),
      ),
    [jobs],
  );

  const jobDepartments = useMemo(
    () => Array.from(new Set(jobs.map((job) => job.department).filter(Boolean))).sort(),
    [jobs],
  );
  const jobLocations = useMemo(
    () => Array.from(new Set(jobs.map((job) => job.location).filter(Boolean))).sort(),
    [jobs],
  );
  const availableJobTypes = useMemo(
    () => Array.from(new Set([...jobTypeOptions, ...jobs.map((job) => job.job_type || job.employment_type).filter(Boolean)])),
    [jobs],
  );
  const jobsPerPage = 8;
  const totalJobPages = Math.max(1, Math.ceil(visibleJobs.length / jobsPerPage));
  const paginatedJobs = useMemo(() => {
    const startIndex = (currentJobPage - 1) * jobsPerPage;
    return visibleJobs.slice(startIndex, startIndex + jobsPerPage);
  }, [currentJobPage, visibleJobs]);
  const jobStart = visibleJobs.length === 0 ? 0 : (currentJobPage - 1) * jobsPerPage + 1;
  const jobEnd = Math.min(currentJobPage * jobsPerPage, visibleJobs.length);
  const jobBoardSummary = useMemo(
    () => ({
      total_jobs: jobs.length,
      open_jobs: jobs.filter((job) => {
        const status = String(job.status || "").trim().toLowerCase();
        return job.active === 1 || job.active === true || status === "open";
      }).length,
      active_candidates: 0,
      strong_matches: 0,
      offers_sent: 0,
      average_time_to_fill: "N/A",
    }),
    [jobs],
  );
  const selectedJobMatches = useMemo(
    () =>
      [...jobMatches]
        .filter((match) => Number(match.job_id ?? selectedJob?.id ?? 0) === Number(selectedJob?.id ?? 0))
        .sort(
          (firstMatch, secondMatch) =>
            Number(secondMatch.match_score || secondMatch.match_percentage || 0) -
            Number(firstMatch.match_score || firstMatch.match_percentage || 0),
        ),
    [jobMatches, selectedJob?.id],
  );
  const strongJobMatches = selectedJobMatches.filter((match) => Number(match.match_score || match.match_percentage || 0) >= 75);
  const otherJobMatches = selectedJobMatches.filter((match) => Number(match.match_score || match.match_percentage || 0) < 75);
  const selectedJobDescriptionSections = getStructuredJobSections(selectedJob);
  const currentJobMatchLabel = formatValue(selectedJob?.title);
  const jobMatchBestScore =
    selectedJobMatches.length > 0
      ? Math.max(...selectedJobMatches.map((match) => Number(match.match_score || match.match_percentage || 0)))
      : null;
  const jobMatchLastMatched = useMemo(() => {
    const timestamps = selectedJobMatches
      .map((match) => match.cache_updated_at || match.cache_created_at || match.updated_at || match.created_at)
      .filter(Boolean)
      .sort((first, second) => String(second).localeCompare(String(first)));

    return timestamps[0] || null;
  }, [selectedJobMatches]);
  const isJobMatching = isLoadingJobMatches;
  const isJobMatchComplete = jobMatchStatus === "success";
  const isJobMatchFailed = jobMatchStatus === "error";
  const matchStageItems = [
    { label: "Preparing job details" },
    { label: "Reading candidate profiles" },
    { label: "Comparing skills and experience" },
    { label: "Ranking candidates" },
    { label: "Saving results" },
  ].map((item, index) => {
    let state = "upcoming";
    if (isJobMatching) {
      if (index < 1) {
        state = "complete";
      } else if (index === jobMatchStageIndex) {
        state = "active";
      } else if (index < jobMatchStageIndex) {
        state = "complete";
      }
    } else if (isJobMatchComplete) {
      state = "complete";
    } else if (isJobMatchFailed) {
      state = index === 0 ? "complete" : "upcoming";
    }

    return { ...item, state };
  });

  const buildJobEditForm = useCallback(
    (job) => ({
      title: String(job?.title || "").trim(),
      location: String(job?.location || "").trim(),
      department: String(job?.department || "").trim(),
      employment_type: String(job?.employment_type || job?.job_type || "").trim(),
      salary: String(job?.salary || "").trim(),
      job_number: String(job?.job_number || job?.job_id || "").trim(),
      description: String(job?.description || job?.full_description || job?.job_description || "").trim(),
      active: job?.active === 1 || job?.active === true || String(job?.status || "").toLowerCase() === "open",
    }),
    [],
  );

  const startJobEdit = () => {
    if (!selectedJob || isJobMatching) {
      return;
    }
    setIsEditingJob(true);
    setJobEditError("");
    setJobEditMessage("");
    setJobEditForm(buildJobEditForm(selectedJob));
  };

  const cancelJobEdit = () => {
    setIsEditingJob(false);
    setIsSavingEditedJob(false);
    setJobEditError("");
    setJobEditMessage("");
    setJobEditForm(buildJobEditForm(selectedJob));
  };

  const handleSaveJobEdit = async (event) => {
    event.preventDefault();
    if (!selectedJob || isSavingEditedJob || isJobMatching) {
      return;
    }

    const trimmedTitle = String(jobEditForm.title || "").trim();
    const trimmedJobNumber = String(jobEditForm.job_number || "").trim();
    if (!trimmedTitle) {
      setJobEditError("Title is required.");
      return;
    }
    if (!trimmedJobNumber && String(selectedJob.job_number || selectedJob.job_id || "").trim()) {
      setJobEditError("Job number cannot be cleared once it exists.");
      return;
    }

    setIsSavingEditedJob(true);
    setJobEditError("");
    setJobEditMessage("");

    const payload = {
      title: trimmedTitle,
      location: String(jobEditForm.location || "").trim(),
      department: String(jobEditForm.department || "").trim(),
      employment_type: String(jobEditForm.employment_type || "").trim(),
      salary: String(jobEditForm.salary || "").trim(),
      job_number: trimmedJobNumber,
      description: String(jobEditForm.description || "").trim(),
      active: Boolean(jobEditForm.active),
    };

    try {
      const response = await api.patch(`/api/scraped-jobs/${selectedJob.id}`, payload);
      const updatedJob = response.data;
      setSelectedJob(updatedJob);
      setJobBoard((currentJobBoard) => ({
        ...currentJobBoard,
        jobs: currentJobBoard.jobs.map((job) => (job.id === updatedJob.id ? { ...job, ...updatedJob } : job)),
      }));
      setJobEditForm(buildJobEditForm(updatedJob));
      setJobEditMessage("Job updated successfully.");
      setIsEditingJob(false);
    } catch (error) {
      console.error(error);
      const detail = error.response?.data?.detail;
      setJobEditError(detail || "Could not update the job.");
    } finally {
      setIsSavingEditedJob(false);
    }
  };

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

  const closeMatchDetails = () => {
    setSelectedMatch(null);
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
      user: "All Users",
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

  const loadJobs = useCallback(async () => {
    setIsLoadingJobs(true);
    setJobBoardError("");

    try {
      const response = await api.get("/api/scraped-jobs");
      const nextJobs = (response.data?.jobs || []).map((job) => ({
        ...job,
        source: job.source || job.company || inferSourceFromUrl(job.url) || "Unknown",
        company: job.company || job.source || inferSourceFromUrl(job.url) || "Unknown",
        job_id: job.job_number,
        department: job.department || job.employment_type || "",
        job_type: job.employment_type || "",
        status: job.active ? "open" : "closed",
        created_at: job.last_scraped,
      }));
      setJobBoard({
        jobs: nextJobs,
        summary: response.data?.summary || emptyJobBoard.summary,
      });
      setSelectedJob(null);
      return true;
    } catch (error) {
      console.error(error);
      if (error.response?.status === 401) {
        setUser(null);
        setJobBoardError("");
      } else {
        setJobBoardError("Could not load job board.");
      }
      return false;
    } finally {
      setIsLoadingJobs(false);
    }
  }, []);

  const loadJobMatches = useCallback(async (job, refresh = false) => {
    if (!job) {
      return false;
    }

    if (jobMatchInProgressRef.current || isEditingJob || isSavingEditedJob) {
      setJobBoardError("");
      setJobMatchMessage(
        jobMatchInProgressRef.current
          ? "Candidate matching is already in progress. Please wait for it to finish."
          : "Finish editing this job before starting a new match.",
      );
      return false;
    }

    jobMatchInProgressRef.current = true;
    const requestId = ++jobMatchRequestRef.current;
    activeJobIdRef.current = job.id;
    setJobMatchStatus("loading");
    setJobMatchErrorMessage("");
    setJobMatchSummary({ analyzedCount: null, highestScore: null });
    setJobMatchStageIndex(2);
    setJobMatchMessage("Matching candidates...");
    setIsLoadingJobMatches(true);

    try {
      const response = await api.get(`/api/scraped-jobs/${job.id}/matches`, {
        params: refresh ? { refresh: 1 } : undefined,
      });
      console.debug("frontend_received_score", {
        job_id: response.data?.job_id,
        candidate_count: response.data?.matches?.length || 0,
        first_match_score: response.data?.matches?.[0]?.match_score,
        first_match_percentage: response.data?.matches?.[0]?.match_percentage,
      });
      const responseJobId = Number(response.data?.job_id ?? response.data?.job?.id ?? response.data?.job?.job_id ?? 0);
      const isCurrentRequest = requestId === jobMatchRequestRef.current && activeJobIdRef.current === job.id;
      if (!isCurrentRequest || responseJobId !== Number(job.id)) {
        return false;
      }
      const nextMatches = (response.data.matches || []).map((match) => ({
        ...match,
        job_id: Number(match.job_id ?? responseJobId ?? job.id),
      }));
      setJobMatches(nextMatches);
      setJobMatchSummary({
        analyzedCount: response.data?.candidate_count ?? response.data?.matches?.length ?? nextMatches.length,
        highestScore:
          nextMatches.length > 0
            ? Math.max(...nextMatches.map((match) => Number(match.match_score || match.match_percentage || 0)))
            : null,
      });
      setJobMatchStageIndex(5);
      setJobMatchStatus("success");
      return true;
    } catch (error) {
      console.error(error);
      if (error.response?.status === 401) {
        setUser(null);
      } else {
        const detail = error.response?.data?.detail;
        setJobMatchErrorMessage(detail || "Matching could not be completed.");
        setJobMatchStatus("error");
      }
      return false;
    } finally {
      jobMatchInProgressRef.current = false;
      setIsLoadingJobMatches(false);
      setJobMatchMessage("");
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
      setJobBoard(emptyJobBoard);
      setSelectedJob(null);
      setJobMatches([]);
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
    if (safeActivePage === "jobBoard" && user) {
      loadJobs();
    }
  }, [loadJobs, safeActivePage, user]);

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

  useEffect(() => stopUploadProgressSimulation, [stopUploadProgressSimulation]);

  useEffect(() => {
    setCurrentAuditPage(1);
  }, [auditFilters, auditLogs]);

  useEffect(() => {
    if (currentAuditPage > totalAuditPages) {
      setCurrentAuditPage(totalAuditPages);
    }
  }, [currentAuditPage, totalAuditPages]);

  useEffect(() => {
    setCurrentJobPage(1);
  }, [jobFilters, jobs]);

  useEffect(() => {
    if (selectedJob) {
      setJobEditForm(buildJobEditForm(selectedJob));
    }
  }, [buildJobEditForm, selectedJob]);

  useEffect(() => {
    if (currentJobPage > totalJobPages) {
      setCurrentJobPage(totalJobPages);
    }
  }, [currentJobPage, totalJobPages]);

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
      setJobBoard(emptyJobBoard);
      setSelectedJob(null);
      setJobMatches([]);
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
      setJobActionMessage("");
      setAuditLogError("");
    }
  };

  const handleUpload = async () => {
    if (isUploading) {
      return;
    }

    if (files.length === 0) {
      setMessage("Please select at least one resume first.");
      return;
    }

    setIsUploading(true);
    setMessage("");
    beginUploadProgressSimulation();

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
            failUploadProgress("Your session expired. Please sign in again.");
            setUser(null);
            setMessage("Your session expired. Please sign in again.");
            return;
          }

          failures.push(`${file.name}${detail ? ` - ${detail}` : ""}`);
        }
      }

      if (failures.length) {
        const failureMessage = `${successes.length} uploaded, ${failures.length} failed. ${failures.join("; ")}`;
        failUploadProgress(failureMessage);
        setMessage(failureMessage);
      } else {
        completeUploadProgress();
        setMessage(
          `${successes.length} resume${successes.length === 1 ? "" : "s"} uploaded successfully.`,
        );
        setFiles([]);
        if (uploadInputRef.current) {
          uploadInputRef.current.value = "";
        }
      }
      await loadCandidates();
    } catch (error) {
      console.error(error);
      const detail = error.response?.data?.detail;
      if (error.response?.status === 401) {
        failUploadProgress("Your session expired. Please sign in again.");
        setUser(null);
        setMessage("Your session expired. Please sign in again.");
      } else if (error.response?.status === 409 && detail) {
        failUploadProgress(detail);
        setMessage(detail);
      } else {
        const failureMessage = detail ? `Upload failed: ${detail}` : "Upload failed.";
        failUploadProgress(failureMessage);
        setMessage(failureMessage);
      }
    } finally {
      setIsUploading(false);
    }
  };

  const selectUploadFiles = (selectedFiles) => {
    if (isUploading) {
      return;
    }

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
      setUploadProgress(0);
      setUploadStatus("idle");
      setUploadError("");
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

    if (isUploading) {
      return;
    }

    selectUploadFiles(event.dataTransfer.files);
  };

  const handleUploadZoneBrowse = (event) => {
    selectUploadFiles(event.target.files);
    event.target.value = "";
  };

  const clearUploadSelection = () => {
    stopUploadProgressSimulation();
    setFiles([]);
    setMessage("");
    setUploadProgress(0);
    setUploadStatus("idle");
    setUploadError("");
    if (uploadInputRef.current) {
      uploadInputRef.current.value = "";
    }
  };

  const updateJobFilter = (key, value) => {
    setJobFilters((currentFilters) => ({
      ...currentFilters,
      [key]: value,
    }));
    setCurrentJobPage(1);
  };

  const handleSelectJob = (job) => {
    if (jobMatchInProgressRef.current || isEditingJob || isSavingEditedJob) {
      setJobBoardError("");
      setJobMatchMessage(
        jobMatchInProgressRef.current
          ? "Candidate matching is already in progress. Please wait for it to finish."
          : "Finish editing this job before starting a new match.",
      );
      return;
    }
    setSelectedMatch(null);
    setSelectedJob(job);
    setJobMatchTab("top");
    setJobMatchStatus("idle");
    setJobMatchSummary({ analyzedCount: null, highestScore: null });
    setJobMatchErrorMessage("");
    setJobMatchStageIndex(0);
    setJobMatchMessage("");
  };

  const openJobDetailsModal = (job) => {
    if (jobMatchInProgressRef.current) {
      setJobBoardError("");
      setJobMatchMessage("Candidate matching is already in progress. Please wait for it to finish.");
      return;
    }
    handleSelectJob(job);
    setIsJobDetailsModalOpen(true);
  };

  const closeJobDetailsModal = () => {
    setIsJobDetailsModalOpen(false);
    setIsEditingJob(false);
    setJobEditError("");
    setJobEditMessage("");
    setJobMatchMessage("");
  };

  const closeJobModal = () => {
    setIsJobModalOpen(false);
    setJobForm(emptyJobForm);
    setJobActionMessage("");
  };

  const handleCreateJob = async (event) => {
    event.preventDefault();
    setIsSavingJob(true);
    setJobActionMessage("");

    try {
      const response = await api.post("/jobs", jobForm);
      closeJobModal();
      await loadJobs();
      setSelectedJob(response.data);
      setJobBoardError("");
    } catch (error) {
      console.error(error);
      const detail = error.response?.data?.detail;
      if (error.response?.status === 401) {
        setUser(null);
      } else {
        setJobActionMessage(detail || "Could not create job requisition.");
      }
    } finally {
      setIsSavingJob(false);
    }
  };

  const handleDeleteJob = async (job) => {
    const confirmed = window.confirm(`Delete ${job.title}?`);
    if (!confirmed) {
      return;
    }

    try {
      await api.delete(`/jobs/${job.id}`);
      await loadJobs();
      if (selectedJob?.id === job.id) {
        setSelectedJob(null);
        setJobMatches([]);
      }
    } catch (error) {
      console.error(error);
      const detail = error.response?.data?.detail;
      setJobBoardError(detail || "Could not delete job.");
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

  const renderUploadPage = () => {
    const shouldShowUploadProgress = uploadStatus !== "idle";
    const progressLabel = `${Math.round(uploadProgress)}%`;

    return (
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
            onDragEnter={() => !isUploading && setIsDraggingUpload(true)}
            onDragLeave={(event) => {
              if (event.currentTarget === event.target) {
                setIsDraggingUpload(false);
              }
            }}
            onDragOver={(event) => {
              event.preventDefault();
              if (!isUploading) {
                setIsDraggingUpload(true);
              }
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
              disabled={isUploading}
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
            <div className="upload-file-panel">
              <div className="upload-file-panel-header">
                <span>Selected files</span>
                <strong>
                  {files.length ? `${files.length} file${files.length === 1 ? "" : "s"} selected` : "No files selected"}
                </strong>
              </div>

              {files.length > 0 && (
                <div className="selected-file-list" aria-label="Selected resume files">
                  {files.map((file) => (
                    <div className="selected-file-row" key={`${file.name}-${file.lastModified}`}>
                      <span className="selected-file-icon" aria-hidden="true">
                        <FileIcon />
                      </span>
                      <div>
                        <strong title={file.name}>{file.name}</strong>
                        <span>{formatFileSize(file.size)}</span>
                      </div>
                      <span className="selected-file-check" aria-hidden="true">
                        <CheckIcon />
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {shouldShowUploadProgress && (
              <div className={`upload-progress-card is-${uploadStatus}`} role="status" aria-live="polite">
                <div className="upload-progress-head">
                  <strong>
                    {uploadStatus === "success"
                      ? "Complete!"
                      : uploadStatus === "error"
                        ? "Upload failed"
                        : "Uploading and parsing resume..."}
                  </strong>
                  <span>{progressLabel}</span>
                </div>

                <div className="upload-progress-track" aria-hidden="true">
                  <span style={{ width: `${Math.min(100, Math.max(0, uploadProgress))}%` }} />
                </div>

                {uploadStatus === "success" ? (
                  <div className="upload-progress-result">
                    <span className="upload-result-icon" aria-hidden="true">
                      <CheckIcon />
                    </span>
                    <div>
                      <strong>Complete!</strong>
                      <p>Resume uploaded and parsed successfully.</p>
                    </div>
                  </div>
                ) : uploadStatus === "error" ? (
                  <p className="upload-progress-error">{uploadError || "Upload failed."}</p>
                ) : null}
              </div>
            )}

            <div className="upload-action-buttons">
              <button className="secondary-button" type="button" onClick={clearUploadSelection} disabled={!files.length && !message && uploadStatus === "idle"}>
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
  };

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

  const renderAuditLogsSection = () => (
    <section className="surface-block audit-log-section">
      <div className="surface-header">
        <div>
          <h2>Audit Logs</h2>
          <p className="subtitle">Events sourced from the existing audit log.</p>
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
          <span>User</span>
          <select
            value={auditFilters.user}
            onChange={(event) => updateAuditFilter("user", event.target.value)}
          >
            <option value="All Users">All Users</option>
            {auditUserOptions.map((auditUser) => (
              <option key={auditUser} value={auditUser}>
                {auditUser}
              </option>
            ))}
          </select>
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
              paginatedAuditLogs.map((log) => (
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

      <div className="table-footer">
        <div className="table-summary">
          {visibleAuditLogs.length === 0
            ? "Showing 0 audit logs"
            : `Showing ${auditStart}-${auditEnd} of ${visibleAuditLogs.length} audit logs`}
        </div>

        <div className="pagination-controls">
          <button
            className="pagination-button"
            type="button"
            onClick={() => setCurrentAuditPage((page) => Math.max(page - 1, 1))}
            disabled={currentAuditPage === 1}
            aria-label="Previous audit log page"
          >
            <ChevronLeftIcon />
          </button>
          <span className="pagination-status">
            Page {currentAuditPage} of {totalAuditPages}
          </span>
          <button
            className="pagination-button"
            type="button"
            onClick={() => setCurrentAuditPage((page) => Math.min(page + 1, totalAuditPages))}
            disabled={currentAuditPage === totalAuditPages}
            aria-label="Next audit log page"
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

      {renderAuditLogsSection()}
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

  const renderJobBoardPage = () => {
    const summary = jobBoardSummary || emptyJobBoard.summary;
    const jobCards = [
      { label: "Total Jobs", value: formatMetric(summary.total_jobs) },
      { label: "Open Jobs", value: formatMetric(summary.open_jobs) },
      { label: "Active Candidates", value: formatMetric(summary.active_candidates) },
      { label: "Strong Matches", value: formatMetric(summary.strong_matches) },
      { label: "Offers Sent", value: formatMetric(summary.offers_sent) },
      { label: "Average Time to Fill", value: summary.average_time_to_fill || "N/A" },
    ];

    return (
      <section className="page-stack job-board-page">
        <header className="page-header candidates-header">
          <div>
            <p className="section-kicker">Recruiting workspace</p>
            <h1>Job Board</h1>
            <p className="subtitle">Track requisitions, candidate matches, and hiring pipeline coverage.</p>
          </div>

          <button
            className="primary-button add-user-button"
            type="button"
            onClick={() => {
              setJobForm(emptyJobForm);
              setJobActionMessage("");
              setIsJobModalOpen(true);
            }}
          >
            Create Job Requisition
          </button>
        </header>

        {jobBoardError && <p className="error-message">{jobBoardError}</p>}
        {isJobMatching && selectedJob && (
          <div className="job-match-banner" role="status" aria-live="polite">
            <SpinnerIcon />
            <span>
              Matching candidates for {formatValue(selectedJob.title)}. Other matching actions are temporarily disabled.
            </span>
          </div>
        )}

        <div className="metric-grid job-metric-grid">
          {jobCards.map((card) => (
            <MetricCard key={card.label} {...card} />
          ))}
        </div>

        <section className="surface-block job-filter-panel">
          <div className="job-filters">
            <label className="search-field job-search-field">
              <span>Search jobs</span>
              <div className="search-input-wrap">
                <SearchIcon />
                <input
                  type="search"
                  value={jobFilters.query}
                  onChange={(event) => updateJobFilter("query", event.target.value)}
                  placeholder="Search title, keyword, or job ID..."
                />
              </div>
            </label>

            <label className="audit-filter">
              <span>Source</span>
              <select value={jobFilters.source} onChange={(event) => updateJobFilter("source", event.target.value)}>
                <option>All Sources</option>
                {jobSources.map((source) => (
                  <option key={source}>{source}</option>
                ))}
              </select>
            </label>

            <label className="audit-filter">
              <span>Department</span>
              <select
                value={jobFilters.department}
                onChange={(event) => updateJobFilter("department", event.target.value)}
              >
                <option>All Departments</option>
                {jobDepartments.map((department) => (
                  <option key={department}>{department}</option>
                ))}
              </select>
            </label>

            <label className="audit-filter">
              <span>Location</span>
              <select
                value={jobFilters.location}
                onChange={(event) => updateJobFilter("location", event.target.value)}
              >
                <option>All Locations</option>
                {jobLocations.map((location) => (
                  <option key={location}>{location}</option>
                ))}
              </select>
            </label>

            <label className="audit-filter">
              <span>Status</span>
              <select
                value={jobFilters.status}
                onChange={(event) => updateJobFilter("status", event.target.value)}
              >
                <option>All Statuses</option>
                {jobStatusOptions.map((status) => (
                  <option key={status} value={status}>
                    {formatLabel(status)}
                  </option>
                ))}
              </select>
            </label>

            <label className="audit-filter">
              <span>Job Type</span>
              <select
                value={jobFilters.jobType}
                onChange={(event) => updateJobFilter("jobType", event.target.value)}
              >
                <option>All Types</option>
                {availableJobTypes.map((jobType) => (
                  <option key={jobType}>{jobType}</option>
                ))}
              </select>
            </label>
          </div>
        </section>

        <div className="job-board-layout">
          <section className="surface-block">
            <div className="surface-header">
              <div>
                <h2>Job Requisitions</h2>
                <p className="subtitle">Open roles sourced from the recruiting database.</p>
              </div>
            </div>

            <div className="table-shell job-table-shell">
              <table className="data-table job-table">
                <thead>
                  <tr>
                <th>Job Title</th>
                <th>Company</th>
                <th>Source</th>
                <th>Location</th>
                <th>Employment Type</th>
                <th>Job Number</th>
                <th>Salary</th>
                <th>Active</th>
                    <th>Last Scraped</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.length === 0 ? (
                    <tr>
                      <td className="empty-state" colSpan="10">
                        {isLoadingJobs ? "Loading jobs..." : "No scraped jobs found yet."}
                      </td>
                    </tr>
                  ) : visibleJobs.length === 0 ? (
                    <tr>
                      <td className="empty-state" colSpan="10">
                        No jobs match your filters.
                      </td>
                    </tr>
                  ) : (
                    paginatedJobs.map((job) => (
                      <tr
                        key={job.id}
                        className={[
                          selectedJob?.id === job.id ? "is-selected-row" : "",
                          isJobMatching && selectedJob?.id !== job.id ? "is-job-row-disabled" : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                        onClick={() => {
                          if (isJobMatching && selectedJob?.id !== job.id) {
                            return;
                          }
                          handleSelectJob(job);
                        }}
                      >
                        <td>
                          <button
                            className="job-title-button"
                            type="button"
                            onClick={() => handleSelectJob(job)}
                            disabled={isJobMatching && selectedJob?.id !== job.id}
                          >
                            <strong>{formatValue(job.title)}</strong>
                          </button>
                        </td>
                        <td><TableValue value={job.company || "Unknown"} /></td>
                        <td><TableValue value={job.source || "Unknown"} /></td>
                        <td><TableValue value={job.location} /></td>
                        <td><TableValue value={job.employment_type} /></td>
                        <td><TableValue value={job.job_number} /></td>
                        <td><TableValue value={job.salary} /></td>
                        <td>{job.active ? "Yes" : "No"}</td>
                        <td><TableValue value={job.last_scraped} /></td>
                        <td>
                          <button
                            className="view-button"
                            type="button"
                            onClick={() => openJobDetailsModal(job)}
                            disabled={isJobMatching && selectedJob?.id !== job.id}
                          >
                            View
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <div className="table-footer">
              <div className="table-summary">
                {visibleJobs.length === 0
                  ? "Showing 0 jobs"
                  : `Showing ${jobStart}-${jobEnd} of ${visibleJobs.length} jobs`}
              </div>

              <div className="pagination-controls">
                <button
                  className="pagination-button"
                  type="button"
                  onClick={() => setCurrentJobPage((page) => Math.max(page - 1, 1))}
                  disabled={currentJobPage === 1}
                  aria-label="Previous job page"
                >
                  <ChevronLeftIcon />
                </button>
                <span className="pagination-status">
                  Page {currentJobPage} of {totalJobPages}
                </span>
                <button
                  className="pagination-button"
                  type="button"
                  onClick={() => setCurrentJobPage((page) => Math.min(page + 1, totalJobPages))}
                  disabled={currentJobPage === totalJobPages}
                  aria-label="Next job page"
                >
                  <ChevronRightIcon />
                </button>
              </div>
            </div>
          </section>

          <aside className="job-detail-panel">
            {selectedJob ? (
              <>
                <section className="detail-card">
                  <div className="job-detail-header">
                    <div>
                      <span>Job Summary</span>
                      <h2>{formatValue(selectedJob.title)}</h2>
                      <p>{[selectedJob.location, selectedJob.employment_type, selectedJob.job_number].filter(Boolean).join(" - ")}</p>
                    </div>
                    <a
                      className="primary-button"
                      href={selectedJob.url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open Original Job
                    </a>
                  </div>

                  <div className="job-detail-meta">
                    <span>Location: {formatValue(selectedJob.location)}</span>
                    <span>Employment Type: {formatValue(selectedJob.employment_type || selectedJob.job_type)}</span>
                    <span>Job Number: {formatValue(selectedJob.job_number || selectedJob.job_id)}</span>
                    <span>Salary: {formatValue(selectedJob.salary)}</span>
                  </div>

                  <p className="job-description">
                    {(selectedJob.description || "").slice(0, 500) || "No job description available."}
                  </p>
                </section>

                <div className="job-detail-header">
                  <div>
                    <span>Candidate Matches</span>
                    <h2>Best matching resumes</h2>
                    <p>Sorted from highest percentage to lowest.</p>
                  </div>
                </div>

                {isLoadingJobMatches ? (
                  <p className="empty-state">Loading candidate matches...</p>
                ) : jobMatches.length === 0 ? (
                  <p className="empty-state">No resumes have been uploaded.</p>
                ) : strongJobMatches.length === 0 ? (
                  <>
                    <p className="empty-state">No strong matches found.</p>
                    {otherJobMatches.length > 0 && (
                      <details className="detail-card">
                        <summary>Other Candidates</summary>
                        <div className="match-card-list">
                          {otherJobMatches.map((match) => {
                            const score = Number(match.match_percentage) || 0;
                            const matchClass = score >= 90 ? "is-strong" : score >= 75 ? "is-warning" : "is-danger";

                            return (
                              <article className="match-card" key={match.candidate_id}>
                                <div className="match-card-head">
                                  <div className={`match-ring ${matchClass}`} style={{ "--match": score }}>
                                    <span>{formatPercent(score)}</span>
                                  </div>
                                  <div>
                                    <h3>{formatValue(match.candidate_name)}</h3>
                                    <p>{formatValue(match.current_position)}</p>
                                  </div>
                                </div>
                                <div className="match-card-meta">
                                  <span>{formatYears(match.years_experience)} years of experience</span>
                                </div>
                                <div className="match-skill-group">
                                  <span>Skills Matched</span>
                                  <div className="skill-tag-list">
                                    {(match.matched_skills || []).length ? (
                                      match.matched_skills.map((skill) => (
                                        <span className="skill-tag" key={skill}>{skill}</span>
                                      ))
                                    ) : (
                                      <span className="skill-tag is-muted">None</span>
                                    )}
                                  </div>
                                </div>
                                <div className="match-skill-group">
                                  <span>Missing Skills</span>
                                  <div className="skill-tag-list">
                                    {(match.missing_skills || []).length ? (
                                      match.missing_skills.map((skill) => (
                                        <span className="skill-tag is-missing" key={skill}>{skill}</span>
                                      ))
                                    ) : (
                                      <span className="skill-tag">None</span>
                                    )}
                                  </div>
                                </div>
                                <button className="secondary-button" type="button" onClick={() => setSelectedMatch(match)}>
                                  View Match
                                </button>
                              </article>
                            );
                          })}
                        </div>
                      </details>
                    )}
                  </>
                ) : (
                  <>
                    <div className="match-card-list">
                      {strongJobMatches.map((match) => {
                        const score = Number(match.match_percentage) || 0;
                        const matchClass = score >= 90 ? "is-strong" : score >= 75 ? "is-warning" : "is-danger";

                        return (
                          <article className="match-card" key={match.candidate_id}>
                            <div className="match-card-head">
                              <div className={`match-ring ${matchClass}`} style={{ "--match": score }}>
                                <span>{formatPercent(score)}</span>
                              </div>
                              <div>
                                <h3>{formatValue(match.candidate_name)}</h3>
                                <p>{formatValue(match.current_position)}</p>
                              </div>
                            </div>
                            <div className="match-card-meta">
                              <span>{formatYears(match.years_experience)} years of experience</span>
                            </div>
                            <div className="match-skill-group">
                              <span>Skills Matched</span>
                              <div className="skill-tag-list">
                                {(match.matched_skills || []).length ? (
                                  match.matched_skills.map((skill) => (
                                    <span className="skill-tag" key={skill}>{skill}</span>
                                  ))
                                ) : (
                                  <span className="skill-tag is-muted">None</span>
                                )}
                              </div>
                            </div>
                            <div className="match-skill-group">
                              <span>Missing Skills</span>
                              <div className="skill-tag-list">
                                {(match.missing_skills || []).length ? (
                                  match.missing_skills.map((skill) => (
                                    <span className="skill-tag is-missing" key={skill}>{skill}</span>
                                  ))
                                ) : (
                                  <span className="skill-tag">None</span>
                                )}
                              </div>
                            </div>
                            <button className="secondary-button" type="button" onClick={() => setSelectedMatch(match)}>
                              View Match
                            </button>
                          </article>
                        );
                      })}
                    </div>

                    {otherJobMatches.length > 0 && (
                      <details className="detail-card">
                        <summary>Other Candidates</summary>
                        <div className="match-card-list">
                          {otherJobMatches.map((match) => {
                            const score = Number(match.match_percentage) || 0;
                            const matchClass = score >= 90 ? "is-strong" : score >= 75 ? "is-warning" : "is-danger";

                            return (
                              <article className="match-card" key={match.candidate_id}>
                                <div className="match-card-head">
                                  <div className={`match-ring ${matchClass}`} style={{ "--match": score }}>
                                    <span>{formatPercent(score)}</span>
                                  </div>
                                  <div>
                                    <h3>{formatValue(match.candidate_name)}</h3>
                                    <p>{formatValue(match.current_position)}</p>
                                  </div>
                                </div>
                                <div className="match-card-meta">
                                  <span>{formatYears(match.years_experience)} years of experience</span>
                                </div>
                                <div className="match-skill-group">
                                  <span>Skills Matched</span>
                                  <div className="skill-tag-list">
                                    {(match.matched_skills || []).length ? (
                                      match.matched_skills.map((skill) => (
                                        <span className="skill-tag" key={skill}>{skill}</span>
                                      ))
                                    ) : (
                                      <span className="skill-tag is-muted">None</span>
                                    )}
                                  </div>
                                </div>
                                <div className="match-skill-group">
                                  <span>Missing Skills</span>
                                  <div className="skill-tag-list">
                                    {(match.missing_skills || []).length ? (
                                      match.missing_skills.map((skill) => (
                                        <span className="skill-tag is-missing" key={skill}>{skill}</span>
                                      ))
                                    ) : (
                                      <span className="skill-tag">None</span>
                                    )}
                                  </div>
                                </div>
                                <button className="secondary-button" type="button" onClick={() => setSelectedMatch(match)}>
                                  View Match
                                </button>
                              </article>
                            );
                          })}
                        </div>
                      </details>
                    )}
                  </>
                )}
              </>
            ) : (
              <p className="empty-state">Select a job to view candidate matches.</p>
            )}
          </aside>
        </div>
      </section>
    );
  };

  const renderJobMatchCard = (match) => {
    const score = Number(match.match_score || match.match_percentage) || 0;
    const matchClass = score >= 90 ? "is-strong" : score >= 75 ? "is-warning" : "is-danger";

    return (
      <article className="match-card" key={match.candidate_id}>
        <div className="match-card-head">
          <div className={`match-ring ${matchClass}`} style={{ "--match": score }}>
            <span>{formatPercent(score)}</span>
          </div>
          <div>
            <h3>{formatValue(match.candidate_name)}</h3>
            <p>{formatValue(match.current_position)}</p>
          </div>
        </div>

        <div className="match-card-meta">
          <span>{formatYears(match.years_experience)} years of experience</span>
          <span>{formatValue(match.match_source || match.source)}</span>
          <span>{match.is_cached ? "Cached" : "New"}</span>
        </div>

        <div className="match-skill-group">
          <span>Match Level</span>
          <div className="skill-tag-list">
            <span className="skill-tag">{formatValue(match.match_level)}</span>
            <span className="skill-tag">{formatValue(match.recommended_action)}</span>
          </div>
        </div>

        <div className="match-skill-group">
          <span>Skills Matched</span>
          <div className="skill-tag-list">
            {(match.matched_skills || []).length ? (
              match.matched_skills.map((skill) => (
                <span className="skill-tag" key={skill}>{skill}</span>
              ))
            ) : (
              <span className="skill-tag is-muted">None</span>
            )}
          </div>
        </div>

        <div className="match-skill-group">
          <span>Missing Skills</span>
          <div className="skill-tag-list">
            {(match.missing_skills || []).length ? (
              match.missing_skills.map((skill) => (
                <span className="skill-tag is-missing" key={skill}>{skill}</span>
              ))
            ) : (
              <span className="skill-tag">None</span>
            )}
          </div>
        </div>

        <div className="match-skill-group">
          <span>Recruiter Summary</span>
          <p className="job-description">{formatValue(match.recruiter_summary)}</p>
        </div>

        <button className="secondary-button" type="button" onClick={() => handleViewCandidate({ id: match.candidate_id })}>
          View Candidate
        </button>

        <button className="secondary-button" type="button" onClick={() => setSelectedMatch(match)}>
          View Match
        </button>
      </article>
    );
  };

  const renderModalMatchCard = (match, index) => {
    const score = Number(match.match_score || match.match_percentage || 0);
    const matchLevel = formatValue(match.match_level);
    const isTopMatch = index === 0;
    const currentPosition = formatValue(match.current_position);
    const matchSource = formatValue(match.match_source || match.source);
    const recommendedAction = formatValue(match.recommended_action);
    const matchedSkills = (match.matched_skills || []).filter(Boolean);
    const missingSkills = (match.missing_skills || []).filter(Boolean);
    const preferredSkills = (match.preferred_skills || []).filter(Boolean);

    const detailItems = [
      {
        label: "Matched skills",
        value: matchedSkills.length ? matchedSkills : [],
        emptyText: "No matched skills identified",
        tone: "positive",
      },
      {
        label: "Missing skills",
        value: missingSkills.length ? missingSkills : [],
        emptyText: "No missing skills identified",
        tone: "negative",
      },
      {
        label: "Preferred skills",
        value: preferredSkills.length ? preferredSkills : [],
        emptyText: "No preferred skills identified",
        tone: "neutral",
      },
    ];

    return (
      <article className={`match-result-card ${isTopMatch ? "is-top-match" : ""}`} key={`${match.candidate_id || match.candidate_name || index}`}>
        <div className="match-result-card-header">
          <div className="match-result-rank" aria-label={`Rank ${index + 1}`}>
            #{index + 1}
          </div>
          <div className="match-result-title">
            <div className="match-result-title-row">
              <h4>{formatValue(match.candidate_name)}</h4>
              {isTopMatch && <span className="match-result-top-badge">Top Match</span>}
            </div>
            <p>{currentPosition}</p>
          </div>
          <div className="match-result-score">
            <strong>{formatPercent(score)}</strong>
            <span>{matchLevel}</span>
          </div>
        </div>

        <div className="match-result-meta">
          <span className={`match-level-pill match-level-${String(match.match_level || "").toLowerCase().replace(/\s+/g, "-") || "neutral"}`}>
            {matchLevel}
          </span>
          <span>{recommendedAction}</span>
          {matchSource !== "Not found" && <span>{matchSource}</span>}
        </div>

        <details className="match-result-details">
          <summary>View Match Details</summary>
          <div className="match-result-details-body">
            <div className="match-result-grid">
              <div className="match-result-field">
                <span>Experience alignment</span>
                <strong>{formatValue(match.years_of_experience ? `Required: ${match.years_of_experience.required_years ?? "not specified"}, Candidate: ${match.years_of_experience.candidate_years ?? "not found"}` : "Not found")}</strong>
              </div>
              <div className="match-result-field">
                <span>Title fit</span>
                <strong>
                  {match.job_title_fit
                    ? `${formatValue(match.job_title_fit.candidate_title)} against ${formatValue(match.job_title_fit.job_title)}`
                    : "Not found"}
                </strong>
              </div>
              <div className="match-result-field">
                <span>Industry match</span>
                <strong>
                  {match.industry_match
                    ? match.industry_match.industry_match
                      ? "Matched"
                      : "No direct match"
                    : "Not found"}
                </strong>
              </div>
              <div className="match-result-field">
                <span>Education</span>
                <strong>
                  {match.education_match
                    ? match.education_match.candidate_education || "The candidate's education was not extracted."
                    : "Not found"}
                </strong>
              </div>
              <div className="match-result-field">
                <span>Certifications</span>
                <strong>
                  {match.certification_match
                    ? (match.certification_match.matched_certifications || []).length
                      ? match.certification_match.matched_certifications.join(", ")
                      : "No matching certifications were found."
                    : "Not found"}
                </strong>
              </div>
              <div className="match-result-field">
                <span>Location match</span>
                <strong>
                  {match.location_match
                    ? match.location_match.candidate_location_match
                      ? "Aligned with job location"
                      : "No location alignment"
                    : "Not found"}
                </strong>
              </div>
            </div>

            <div className="match-result-skills">
              {detailItems.map((item) => (
                <div className="match-result-skill-group" key={item.label}>
                  <span>{item.label}</span>
                  <div className="skill-tag-list">
                    {item.value.length ? (
                      item.value.map((skill) => (
                        <span
                          className={`skill-tag ${item.tone === "positive" ? "is-positive" : item.tone === "negative" ? "is-missing" : "is-neutral"}`}
                          key={skill}
                        >
                          {skill}
                        </span>
                      ))
                    ) : (
                      <span className="skill-tag is-muted">{item.emptyText}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <div className="match-result-summary">
              <div>
                <span>Recruiter summary</span>
                <p>{formatValue(match.recruiter_summary)}</p>
              </div>
            </div>

            <div className="match-result-actions">
              <button className="secondary-button" type="button" onClick={() => handleViewCandidate({ id: match.candidate_id })}>
                View Candidate
              </button>
              <button className="secondary-button" type="button" onClick={() => setSelectedMatch(match)}>
                View Match Details
              </button>
            </div>
          </div>
        </details>
      </article>
    );
  };

  const getRequirementFallback = (match, key, fallbackText) => {
    const status = match?.extraction_status?.job?.[key];
    if (status === "not specified") {
      return fallbackText;
    }

    return fallbackText;
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

        <div className="metric-grid">
          {securityCards.map((card) => (
            <MetricCard key={card.label} {...card} />
          ))}
        </div>

        {renderAuditLogsSection()}
      </section>
    );
  };

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
    jobBoard: renderJobBoardPage(),
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

      {isJobDetailsModalOpen && selectedJob && (
        <div className="modal-overlay" onClick={closeJobDetailsModal}>
          <section
            className="details-modal job-details-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="job-details-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="job-details-header">
              <div className="job-details-heading">
                <div className="job-details-title-row">
                  <h2 id="job-details-title">{formatValue(selectedJob.title)}</h2>
                </div>

                <div className="job-details-badges" aria-label="Job status details">
                  <span className="job-detail-badge job-detail-badge--success">Active</span>
                  <span className="job-detail-badge job-detail-badge--neutral">
                    {formatValue(selectedJob.employment_type || selectedJob.job_type)}
                  </span>
                  <span className="job-detail-badge job-detail-badge--neutral">
                    Job #{formatValue(selectedJob.job_number || selectedJob.job_id)}
                  </span>
                </div>
              </div>

              <div className="job-details-header-actions">
                <button className="modal-close job-details-close" type="button" onClick={closeJobDetailsModal}>
                  <AppIcon>
                    <path d="M6.5 6.5 17.5 17.5" />
                    <path d="M17.5 6.5 6.5 17.5" />
                  </AppIcon>
                  <span>Close</span>
                </button>
                <button className="primary-button job-details-edit-button" type="button" onClick={startJobEdit} disabled={isJobMatching || isEditingJob || isSavingEditedJob}>
                  <EditIcon />
                  <span>Edit Job</span>
                </button>
              </div>
            </header>

            {(isJobMatching || isJobMatchComplete || isJobMatchFailed) && (
              <section
                className={`match-feedback-card ${isJobMatching ? "is-loading" : isJobMatchComplete ? "is-success" : "is-error"}`}
                aria-live="polite"
              >
                <div className="match-feedback-header">
                  <div className="match-feedback-icon" aria-hidden="true">
                    {isJobMatching ? <SpinnerIcon /> : isJobMatchComplete ? <CheckIcon /> : <AppIcon><path d="M12 9v4" /><path d="M12 16.5h.01" /><path d="M4.5 19.5h15L12 4.5 4.5 19.5Z" /></AppIcon>}
                  </div>
                  <div>
                    <h3>
                      {isJobMatching
                        ? "Finding the best candidates..."
                        : isJobMatchComplete
                          ? "Matching Complete"
                          : "Matching could not be completed."}
                    </h3>
                    <p>
                      {isJobMatching
                        ? "Comparing this job with uploaded candidate profiles."
                        : isJobMatchComplete
                          ? "The latest match results are ready for review."
                          : jobMatchErrorMessage || "The matching request did not finish successfully."}
                    </p>
                  </div>
                </div>

                <div className="match-feedback-body">
                  <div className="match-feedback-job">
                    <span>Job being matched</span>
                    <strong>{currentJobMatchLabel}</strong>
                    <p>Other matching actions are temporarily disabled while this session is active.</p>
                  </div>

                  <ul className="match-stage-list">
                    {matchStageItems.map((stage) => (
                      <li key={stage.label} className={`match-stage-item is-${stage.state}`}>
                        <span className="match-stage-marker" aria-hidden="true">
                          {stage.state === "complete" ? <CheckIcon /> : stage.state === "active" ? <SpinnerIcon /> : <span />}
                        </span>
                        <span>{stage.label}</span>
                      </li>
                    ))}
                  </ul>

                  {isJobMatchComplete && (
                    <div className="match-feedback-summary">
                      <div>
                        <span>Analyzed candidates</span>
                        <strong>{jobMatchSummary.analyzedCount ?? jobMatches.length ?? 0}</strong>
                      </div>
                      <div>
                        <span>Highest match</span>
                        <strong>{formatPercent(jobMatchSummary.highestScore ?? jobMatchBestScore ?? 0)}</strong>
                      </div>
                      <button className="secondary-button" type="button" onClick={() => setJobMatchTab("top")}>
                        View Matches
                      </button>
                    </div>
                  )}

                  {isJobMatchFailed && (
                    <div className="match-feedback-actions">
                      <button
                        className="primary-button"
                        type="button"
                        onClick={() => loadJobMatches(selectedJob, true)}
                        disabled={isJobMatching}
                      >
                        Try Again
                      </button>
                    </div>
                  )}
                </div>
              </section>
            )}

            <div className="job-details-body">
              <aside className="job-details-sidebar">
                <section className="detail-card job-summary-card">
                  <div className="detail-card-header">
                    <div className="detail-card-kicker">
                      <JobTitleIcon />
                      <span>Job Summary</span>
                    </div>
                    <p>Quick facts at a glance.</p>
                  </div>
                  <dl className="job-summary-list">
                    <DetailItem icon={JobTitleIcon} label="Job Title" value={selectedJob.title} />
                    <DetailItem icon={UsersIcon} label="Company" value={selectedJob.company || "Not found"} />
                    <DetailItem icon={LocationIcon} label="Location" value={selectedJob.location} />
                    <DetailItem icon={DepartmentIcon} label="Department" value={selectedJob.department} />
                    <DetailItem icon={BriefcaseIcon} label="Employment Type" value={selectedJob.employment_type || selectedJob.job_type} />
                    <DetailItem icon={CurrencyIcon} label="Salary" value={formatSalary(selectedJob.salary)} />
                    <DetailItem icon={CalendarIcon} label="Schedule" value={selectedJob.schedule || selectedJob.job_schedule || "Not found"} />
                    {selectedJobDescriptionSections.experienceYears && (
                      <DetailItem icon={SparkIcon} label="Experience Required" value={selectedJobDescriptionSections.experienceYears} />
                    )}
                    <DetailItem icon={HashIcon} label="Job Number" value={selectedJob.job_number || selectedJob.job_id} />
                    <DetailItem icon={ScrapeIcon} label="Last Updated" value={formatDate(selectedJob.last_scraped)} />
                  </dl>
                </section>

                <section className="detail-card quick-actions-card">
                  <div className="detail-card-header">
                    <div className="detail-card-kicker">
                      <SparkIcon />
                      <span>Quick Actions</span>
                    </div>
                  </div>
                  <div className="job-details-actions">
                    <button
                      className="primary-button job-details-action-button"
                      type="button"
                      onClick={() => loadJobMatches(selectedJob, true)}
                      disabled={isLoadingJobMatches || isEditingJob || isSavingEditedJob}
                    >
                      {isLoadingJobMatches ? <SpinnerIcon /> : <AppIcon><path d="M11.5 5.5a6 6 0 1 0 6 6" /><path d="m16.5 4.5 1.5 1.5-1.5 1.5" /></AppIcon>}
                      <span>{isLoadingJobMatches ? (jobMatches.length > 0 ? "Recalculating..." : "Matching Candidates...") : "Match Candidates"}</span>
                    </button>
                    <button className="secondary-button job-details-action-button" type="button" onClick={startJobEdit} disabled={isJobMatching || isEditingJob || isSavingEditedJob}>
                      <AppIcon>
                        <path d="m5.5 15 8.9-8.9a1.4 1.4 0 0 1 2 0l1.5 1.5a1.4 1.4 0 0 1 0 2L9 18.1 5 19l.5-4Z" />
                      </AppIcon>
                      <span>Edit Job</span>
                    </button>
                    <a
                      className="secondary-button job-details-action-button job-details-link-button"
                      href={selectedJob.url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <AppIcon>
                        <path d="M14 5.5h4.5V10" />
                        <path d="M9.5 14.5 18.5 5.5" />
                        <path d="M13 5.5H6.5A2 2 0 0 0 4.5 7.5v10A2 2 0 0 0 6.5 19.5h10A2 2 0 0 0 18.5 17.5V11" />
                      </AppIcon>
                      <span>Open Original Job</span>
                    </a>
                  </div>
                </section>

                <section className="detail-card status-card">
                  <div className="detail-card-header">
                    <div className="detail-card-kicker">
                      <SparkIcon />
                      <span>Matching Status</span>
                    </div>
                  </div>
                  <div className="status-panel">
                    <div className="status-panel-copy">
                      <strong>
                        {isLoadingJobMatches
                          ? "Matching in progress"
                          : strongJobMatches.length > 0
                            ? "Strong matches available"
                            : "This job has not been matched yet"}
                      </strong>
                      <p>
                        {isLoadingJobMatches
                          ? "Please wait while candidate resumes are analyzed."
                          : strongJobMatches.length > 0
                            ? "Review the strongest candidate resumes below."
                            : "Click Match Candidates to find the best matches."}
                      </p>
                    </div>
                    <div className={`status-pill ${isLoadingJobMatches ? "status-warning" : strongJobMatches.length > 0 ? "status-success" : "status-neutral"}`}>
                      {isLoadingJobMatches ? "Analyzing" : strongJobMatches.length > 0 ? "Ready" : "Pending"}
                    </div>
                  </div>
                  {jobMatchMessage && <p className="status-message">{jobMatchMessage}</p>}
                </section>
              </aside>

              <section className="job-details-content">
                {isEditingJob ? (
                  <form className="detail-card job-edit-form" onSubmit={handleSaveJobEdit}>
                    <div className="detail-card-header">
                      <div className="detail-card-kicker">
                        <EditIcon />
                        <span>Edit Job</span>
                      </div>
                      <p>Update supported job details and save the corrections.</p>
                    </div>

                    {jobEditError && <p className="error-message" aria-live="polite">{jobEditError}</p>}
                    {jobEditMessage && <p className="success-message" aria-live="polite">{jobEditMessage}</p>}

                    <div className="job-edit-grid">
                      <label className="job-edit-field">
                        <span>Title *</span>
                        <input
                          type="text"
                          value={jobEditForm.title}
                          onChange={(event) => setJobEditForm((current) => ({ ...current, title: event.target.value }))}
                          required
                        />
                      </label>
                      <label className="job-edit-field">
                        <span>Job Number</span>
                        <input
                          type="text"
                          value={jobEditForm.job_number}
                          onChange={(event) => setJobEditForm((current) => ({ ...current, job_number: event.target.value }))}
                        />
                      </label>
                      <label className="job-edit-field">
                        <span>Location</span>
                        <input
                          type="text"
                          value={jobEditForm.location}
                          onChange={(event) => setJobEditForm((current) => ({ ...current, location: event.target.value }))}
                        />
                      </label>
                      <label className="job-edit-field">
                        <span>Department</span>
                        <input
                          type="text"
                          value={jobEditForm.department}
                          onChange={(event) => setJobEditForm((current) => ({ ...current, department: event.target.value }))}
                        />
                      </label>
                      <label className="job-edit-field">
                        <span>Employment Type</span>
                        <input
                          type="text"
                          value={jobEditForm.employment_type}
                          onChange={(event) => setJobEditForm((current) => ({ ...current, employment_type: event.target.value }))}
                        />
                      </label>
                      <label className="job-edit-field">
                        <span>Salary</span>
                        <input
                          type="text"
                          value={jobEditForm.salary}
                          onChange={(event) => setJobEditForm((current) => ({ ...current, salary: event.target.value }))}
                        />
                      </label>
                    </div>

                    <label className="job-edit-field job-edit-field--full">
                      <span>Job Overview / Description</span>
                      <textarea
                        rows={8}
                        value={jobEditForm.description}
                        onChange={(event) => setJobEditForm((current) => ({ ...current, description: event.target.value }))}
                      />
                    </label>

                    <label className="job-edit-check">
                      <input
                        type="checkbox"
                        checked={jobEditForm.active}
                        onChange={(event) => setJobEditForm((current) => ({ ...current, active: event.target.checked }))}
                      />
                      <span>Active status</span>
                    </label>

                    <div className="job-edit-actions">
                      <button className="secondary-button" type="button" onClick={cancelJobEdit} disabled={isSavingEditedJob}>
                        Cancel
                      </button>
                      <button className="primary-button" type="submit" disabled={isSavingEditedJob}>
                        {isSavingEditedJob ? <SpinnerIcon /> : null}
                        <span>{isSavingEditedJob ? "Saving..." : "Save Changes"}</span>
                      </button>
                    </div>
                  </form>
                ) : (
                  <>
                    <section className="detail-card">
                      <div className="detail-card-header">
                        <div className="detail-card-kicker">
                          <JobTitleIcon />
                          <span>Job Overview</span>
                        </div>
                      </div>
                      <div className="job-section-stack">
                        <JobDetailSection title="Job Overview" content={selectedJobDescriptionSections.overview} />
                      </div>
                    </section>

                    <section className="detail-card">
                      <div className="detail-card-header">
                        <div className="detail-card-kicker">
                          <PeopleIcon />
                          <span>Responsibilities</span>
                        </div>
                      </div>
                      <div className="job-section-stack">
                        <JobDetailSection
                          title="Responsibilities"
                          content={selectedJobDescriptionSections.responsibilities}
                        />
                      </div>
                    </section>

                    <section className="detail-card">
                      <div className="detail-card-header">
                        <div className="detail-card-kicker">
                          <BriefcaseIcon />
                          <span>Qualifications</span>
                        </div>
                      </div>
                      <div className="job-section-stack">
                        <JobDetailSection
                          title="Qualifications"
                          content={selectedJobDescriptionSections.qualifications}
                        />
                      </div>
                    </section>

                    {selectedJobDescriptionSections.experienceRequired?.length > 0 && (
                      <section className="detail-card">
                        <div className="detail-card-header">
                          <div className="detail-card-kicker">
                            <SparkIcon />
                            <span>Experience Required</span>
                          </div>
                        </div>
                        <div className="job-section-stack">
                          <JobDetailSection
                            title="Experience Required"
                            content={selectedJobDescriptionSections.experienceRequired}
                          />
                        </div>
                      </section>
                    )}

                    {selectedJobDescriptionSections.benefits?.length > 0 && (
                      <section className="detail-card">
                        <div className="detail-card-header">
                          <div className="detail-card-kicker">
                            <SparkIcon />
                            <span>Benefits</span>
                          </div>
                        </div>
                        <div className="job-section-stack">
                          <JobDetailSection title="Benefits" content={selectedJobDescriptionSections.benefits} />
                        </div>
                      </section>
                    )}

                    {selectedJobDescriptionSections.additionalNotes?.length > 0 && (
                      <section className="detail-card">
                        <div className="detail-card-header">
                          <div className="detail-card-kicker">
                            <ScrapeIcon />
                            <span>Additional Notes</span>
                          </div>
                        </div>
                        <div className="job-section-stack">
                          <JobDetailSection
                            title="Additional Notes"
                            content={selectedJobDescriptionSections.additionalNotes}
                          />
                        </div>
                      </section>
                    )}

                    {(jobMatches.length > 0 || isJobMatchComplete || isJobMatchFailed) && (
                      <section className="job-match-results-section" aria-labelledby="job-match-results-title">
                        <div className="detail-card-header job-match-results-header">
                          <div className="detail-card-kicker">
                            <SparkIcon />
                            <h3 id="job-match-results-title">Candidate Matches</h3>
                          </div>
                          <div className="job-match-results-summary">
                            <span className={`job-match-results-badge ${jobMatches.length > 0 ? "is-active" : "is-muted"}`}>
                              {jobMatches.length > 0
                                ? "Results Available"
                                : isJobMatchFailed || isJobMatchComplete
                                  ? "No Matches Found"
                                  : "Matching Complete"}
                            </span>
                            <span>Analyzed: {jobMatchSummary.analyzedCount ?? jobMatches.length ?? 0}</span>
                            <span>Results shown: {jobMatches.length}</span>
                            <span>Highest match: {formatPercent(jobMatchSummary.highestScore ?? jobMatchBestScore ?? 0)}</span>
                            <span>Last matched: {jobMatchLastMatched ? formatDate(jobMatchLastMatched) : "Not found"}</span>
                          </div>
                        </div>

                        {jobMatches.length === 0 ? (
                          <div className="job-match-empty-state">
                            <strong>No candidate matches found.</strong>
                            <p>No uploaded candidates currently match this job.</p>
                            <button className="secondary-button" type="button" onClick={() => loadJobMatches(selectedJob, true)} disabled={isJobMatching}>
                              Recalculate Matches
                            </button>
                          </div>
                        ) : (
                          <div className="match-result-list">
                            {selectedJobMatches.map((match, index) => renderModalMatchCard(match, index))}
                          </div>
                        )}
                      </section>
                    )}
                  </>
                )}
              </section>
            </div>
          </section>
        </div>
      )}

      {selectedMatch && (
        <div className="modal-overlay" onClick={closeMatchDetails}>
          <section
            className="details-modal job-details-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="match-details-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="modal-header">
              <div>
                <h2 id="match-details-title">Match Details</h2>
                <p>{formatValue(selectedMatch.candidate_name)}</p>
              </div>

              <button className="modal-close" type="button" onClick={closeMatchDetails}>
                Close
              </button>
            </header>

            <div className="details-grid">
              <section className="detail-card">
                <h3>Overview</h3>
                <dl>
                  <DetailItem label="Overall Match Score" value={formatPercent(selectedMatch.match_score ?? selectedMatch.match_percentage ?? 0)} />
                  <DetailItem label="Match Level" value={selectedMatch.match_level} />
                  <DetailItem label="Match Source" value={selectedMatch.match_source || selectedMatch.source} />
                  <DetailItem label="Recommended Action" value={selectedMatch.recommended_action} />
                </dl>
              </section>

              <section className="detail-card">
                <h3>Match Breakdown</h3>
                <dl>
                  <DetailItem
                    label="Required Skills"
                    value={(selectedMatch.required_skills || []).length
                      ? selectedMatch.required_skills.join(", ")
                      : formatDetailValue(
                          selectedMatch.extraction_status?.job?.required_skills,
                          "No required skills were specified in the job posting."
                        )}
                  />
                  <DetailItem
                    label="Matched Skills"
                    value={(selectedMatch.matched_skills || []).length ? selectedMatch.matched_skills.join(", ") : "No overlapping skills were found."}
                  />
                  <DetailItem
                    label="Missing Required Skills"
                    value={(selectedMatch.missing_skills || []).length
                      ? selectedMatch.missing_skills.join(", ")
                      : formatDetailValue(
                          selectedMatch.extraction_status?.job?.required_skills,
                          "No required skills were specified in the job posting."
                        )}
                  />
                  <DetailItem
                    label="Preferred Skills"
                    value={(selectedMatch.preferred_skills || []).length
                      ? selectedMatch.preferred_skills.join(", ")
                      : "No preferred skills were specified."}
                  />
                  <DetailItem
                    label="Matched Preferred Skills"
                    value={(selectedMatch.matched_preferred_skills || []).length ? selectedMatch.matched_preferred_skills.join(", ") : "No preferred skills matched."}
                  />
                  <DetailItem
                    label="Job Title Fit"
                    value={
                      selectedMatch.job_title_fit
                        ? `${formatValue(selectedMatch.job_title_fit.candidate_title)} against ${formatValue(selectedMatch.job_title_fit.job_title)}`
                        : "Not found"
                    }
                  />
                  <DetailItem
                    label="Years of Experience"
                    value={
                      selectedMatch.years_of_experience
                        ? `Required: ${selectedMatch.years_of_experience.required_years ?? "not specified"}, Candidate: ${selectedMatch.years_of_experience.candidate_years ?? "not found"}`
                        : "Years of experience requirement not specified in the job posting."
                    }
                  />
                  <DetailItem
                    label="Industry Match"
                    value={
                      selectedMatch.industry_match
                        ? selectedMatch.industry_match.industry_match
                          ? "Matched"
                          : "No direct match"
                        : "Not found"
                    }
                  />
                  <DetailItem
                    label="Certifications"
                    value={
                      selectedMatch.certification_match
                        ? (selectedMatch.certification_match.matched_certifications || []).length
                          ? selectedMatch.certification_match.matched_certifications.join(", ")
                          : "No matching certifications were found."
                        : "The job posting does not specify certifications."
                    }
                  />
                  <DetailItem
                    label="Education"
                    value={
                      selectedMatch.education_match
                        ? selectedMatch.education_match.candidate_education || "The candidate's education was not extracted."
                        : "The job posting does not specify an education requirement."
                    }
                  />
                  <DetailItem
                    label="Location Match"
                    value={
                      selectedMatch.location_match
                        ? selectedMatch.location_match.candidate_location_match
                          ? "Aligned with job location"
                          : "No location alignment"
                        : "The job posting does not specify a location requirement."
                    }
                  />
                </dl>
              </section>

              <section className="detail-card">
                <h3>Recruiter Notes</h3>
                <dl>
                  <DetailItem label="Recruiter Summary" value={selectedMatch.recruiter_summary} />
                </dl>
              </section>

              {selectedMatch.match_source === "Qwen Final Review" ? (
                <section className="detail-card">
                  <h3>Qwen Final Review</h3>
                  <dl>
                    <DetailItem
                      label="Strengths"
                      value={(selectedMatch.qwen_final_review?.strengths || selectedMatch.strengths || []).length ? (selectedMatch.qwen_final_review?.strengths || selectedMatch.strengths).join(", ") : "No strengths were returned by Qwen."}
                    />
                    <DetailItem
                      label="Gaps"
                      value={(selectedMatch.qwen_final_review?.gaps || selectedMatch.gaps || []).length ? (selectedMatch.qwen_final_review?.gaps || selectedMatch.gaps).join(", ") : "No gaps were returned by Qwen."}
                    />
                    <DetailItem
                      label="Recommendation"
                      value={selectedMatch.qwen_final_review?.recommendation || selectedMatch.recommended_action}
                    />
                  </dl>
                </section>
              ) : (
                <section className="detail-card">
                  <h3>Qwen Final Review</h3>
                  <p className="empty-state">This candidate was ranked using the Python matching engine.</p>
                </section>
              )}
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

      {isJobModalOpen && (
        <div className="modal-overlay" onClick={closeJobModal}>
          <section
            className="details-modal job-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="job-modal-title"
            onClick={(event) => event.stopPropagation()}
          >
            <header className="modal-header">
              <div>
                <h2 id="job-modal-title">Create Job Requisition</h2>
                <p>Add a requisition for candidate matching and recruiting coverage.</p>
              </div>

              <button className="modal-close" type="button" onClick={closeJobModal}>
                Close
              </button>
            </header>

            <form className="user-form" onSubmit={handleCreateJob}>
              {jobActionMessage && <p className="error-message">{jobActionMessage}</p>}

              <div className="user-form-grid">
                <label className="audit-filter">
                  <span>Title</span>
                  <input
                    type="text"
                    value={jobForm.title}
                    onChange={(event) => setJobForm((currentForm) => ({ ...currentForm, title: event.target.value }))}
                    required
                  />
                </label>

                <label className="audit-filter">
                  <span>Department</span>
                  <input
                    type="text"
                    value={jobForm.department}
                    onChange={(event) => setJobForm((currentForm) => ({ ...currentForm, department: event.target.value }))}
                  />
                </label>

                <label className="audit-filter">
                  <span>Location</span>
                  <input
                    type="text"
                    value={jobForm.location}
                    onChange={(event) => setJobForm((currentForm) => ({ ...currentForm, location: event.target.value }))}
                  />
                </label>

                <label className="audit-filter">
                  <span>Job Type</span>
                  <select
                    value={jobForm.job_type}
                    onChange={(event) => setJobForm((currentForm) => ({ ...currentForm, job_type: event.target.value }))}
                  >
                    {jobTypeOptions.map((jobType) => (
                      <option key={jobType}>{jobType}</option>
                    ))}
                  </select>
                </label>

                <label className="audit-filter">
                  <span>Status</span>
                  <select
                    value={jobForm.status}
                    onChange={(event) => setJobForm((currentForm) => ({ ...currentForm, status: event.target.value }))}
                  >
                    {jobStatusOptions.map((status) => (
                      <option key={status} value={status}>
                        {formatLabel(status)}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="audit-filter">
                  <span>Salary</span>
                  <input
                    type="text"
                    value={jobForm.salary}
                    onChange={(event) => setJobForm((currentForm) => ({ ...currentForm, salary: event.target.value }))}
                    placeholder="Optional"
                  />
                </label>
              </div>

              <label className="audit-filter">
                <span>Required Skills</span>
                <input
                  type="text"
                  value={jobForm.required_skills}
                  onChange={(event) => setJobForm((currentForm) => ({ ...currentForm, required_skills: event.target.value }))}
                  placeholder="Python, FastAPI, React"
                />
              </label>

              <label className="audit-filter">
                <span>Job Description</span>
                <textarea
                  value={jobForm.description}
                  onChange={(event) => setJobForm((currentForm) => ({ ...currentForm, description: event.target.value }))}
                  rows="5"
                />
              </label>

              <div className="modal-actions">
                <button className="secondary-button" type="button" onClick={closeJobModal}>
                  Cancel
                </button>
                <button className="primary-button" type="submit" disabled={isSavingJob}>
                  {isSavingJob ? "Saving..." : "Save Job"}
                </button>
              </div>
            </form>
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

function DetailItem({ icon: Icon, label, value }) {
  return (
    <div className="detail-item">
      <dt>
        <span className="detail-item-icon" aria-hidden="true">
          <Icon />
        </span>
        <span>{label}</span>
      </dt>
      <dd>{formatValue(value)}</dd>
    </div>
  );
}

function JobDetailSection({ title, content }) {
  const paragraphs = content.length > 0 ? content.flatMap((entry) => toTextParagraphs(entry)) : [];

  return (
    <article className="job-section-card">
      <h3>{title}</h3>
      {paragraphs.length > 0 ? (
        paragraphs.map((paragraph, index) => <p key={`${title}-${index}`}>{paragraph}</p>)
      ) : (
        <p className="job-section-empty">N/A</p>
      )}
    </article>
  );
}

export default App;
