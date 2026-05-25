const SIGNOZ_URL =
  "https://monitoring-develop.solargraf.com/api/v5/query_range";
const HEADERS = {
  "Content-Type": "application/json",
  Authorization:
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3Nzk1NDcxNDAsImlhdCI6MTc3OTU0NTM0MCwiaWQiOiIwMTlkMmVlMC00NDNmLTc2NjgtOTJjOC04Y2IyMDFhYTM5NmIiLCJlbWFpbCI6ImtzYWluaUBlbnBoYXNlZW5lcmd5LmNvbSIsInJvbGUiOiJFRElUT1IiLCJvcmdJZCI6IjAxOWNmYjhkLTlhODctNzRkNC05OGIzLWIyMTliNzNmZjk3YSJ9.apvUX52I9BMZ-KX_wCJXLin6FTz143FgNRqikXMbm7w", // add if needed
};

const askDevin = require("./askDevin");

// Get the error logs from the permit services.
const fetchPermitServiceErrorLogs = async (payload) => {
  try {
    console.log("🔍 Fetching permit service logs...");

    // fetch logs of last 10 days
    const basePayload = getSignozQuery(payload.url, 10 * 60 * 60 * 24 * 1000);
    const baseLogs = await callSigNoz(basePayload);

    console.log(`Found ${baseLogs.length} base logs`);

    const results = [];

    const correlationIdSet = new Set();

    for (const log of baseLogs) {
      // find error logs by unique correlation id;
      const correlationId = log.attributes_string.correlationId;

      // If correlationId is already processed dont refetch logs for it.
      if (correlationIdSet.has(correlationId) || !correlationId) continue;

      correlationIdSet.add(correlationId);
      const expression = `correlationId='${correlationId}' AND (severity_text = 'Error' OR severity_text='Warn' or severity_text='warn')`;
      const contextPayload = getSignozQuery(
        expression,
        10 * 60 * 60 * 24 * 1000
      );
      const errors = await callSigNoz(contextPayload);

      //const errors = contextLogs;
      if (errors.length > 0) {
        results.push({
          baseLog: log,
          errors: errors,
        });
      }
    }

    if (results.length === 0) {
      console.log("❌ No errors found in context");
      return null;
    }

    console.log("✅ Errors found!");

    let fileteredResult = results
      .map((res) => res.errors.map((item) => item.body))
      .filter(Boolean);
    return fileteredResult;
  } catch (err) {
    console.error("🔥 Error:", err);
  }
};

// 🔹 helper: fetch logs
async function callSigNoz(payload) {
  const py = JSON.stringify(payload);
  const res = await fetch(SIGNOZ_URL, {
    method: "POST",
    headers: HEADERS,
    body: JSON.stringify(payload),
  });

  const json = await res.json();

  if (json.status !== "success") {
    throw new Error(JSON.stringify(json));
  }

  return json.data.data.results[0].rows.map((r) => r.data);
}

// method to build the signoz query
function getSignozQuery(signozExpression, timeBeforeCurrentInMillis) {
  const now = Date.now();

  return {
    schemaVersion: "v1",
    start: now - timeBeforeCurrentInMillis, // last 3 hours
    end: now,
    requestType: "raw",
    compositeQuery: {
      queries: [
        {
          type: "builder_query",
          spec: {
            name: "A",
            signal: "logs",
            filter: {
              expression: signozExpression,
            },
            limit: 20,
            order: [
              {
                key: { name: "timestamp" },
                direction: "desc",
              },
            ],
          },
        },
      ],
    },
    formatOptions: {
      formatTableResultForUI: false,
      fillGaps: false,
    },
    variables: {},
  };
}

const url =
  "/projects/342321/proposals/40b1f948-25a9-44ad-a1d4-e78c312f436d/drawings/dwg";

fetchPermitServiceErrorLogs({
  url: url,
})
  .then((res) => {
    console.log("errors: ", res);
    askDevin(JSON.stringify(res), url).then((result) =>
      console.log("AI response: ", result)
    );
  })
  .catch((err) => {
    console.error("ERROR:", err);
  });
