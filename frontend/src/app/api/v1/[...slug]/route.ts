import { NextRequest, NextResponse } from "next/server";

const DEFAULT_LOCALSTACK_HOST = process.env.LOCALSTACK_HOST || "http://localhost:4566";

/**
 * Pure API Gateway Proxy to LocalStack (port 4566).
 * Strictly forwards all frontend requests to the AWS CDK LambdaRestApi deployed in LocalStack.
 * Auto-discovers the active API Gateway <api-id> from LocalStack if a full URL is not specified.
 */
async function resolveLocalStackApiGatewayBaseUrl(customHeaderUrl?: string | null): Promise<string> {
  const configured = (customHeaderUrl || process.env.LOCALSTACK_API_URL || "").trim();
  if (configured && configured.includes("/restapis/") || configured.includes(".execute-api.")) {
    return configured.replace(/\/$/, "");
  }

  const host = (configured || DEFAULT_LOCALSTACK_HOST).replace(/\/$/, "");

  // Query LocalStack API Gateway control plane for the deployed LambdaRestApi id
  const resp = await fetch(`${host}/restapis`, {
    method: "GET",
    headers: {
      Accept: "application/json",
      Authorization:
        "AWS4-HMAC-SHA256 Credential=test/20261117/us-east-1/apigateway/aws4_request, SignedHeaders=host, Signature=test",
    },
    cache: "no-store",
  });

  if (resp.ok) {
    const data = await resp.json();
    const items = data?.item || data?.items || [];
    if (Array.isArray(items) && items.length > 0 && items[0].id) {
      return `${host}/restapis/${items[0].id}/prod/_user_request_`;
    }
  }

  throw new Error(
    `No deployed API Gateway found at ${host}. Run 'docker compose up -d --build', 'cdklocal bootstrap', and 'cdklocal deploy' inside cdk-dev.`
  );
}

async function forwardToLocalStack(
  req: NextRequest,
  subpath: string,
  bodyText?: string
): Promise<NextResponse> {
  try {
    const customUrl = req.headers.get("x-localstack-url");
    const baseUrl = await resolveLocalStackApiGatewayBaseUrl(customUrl);
    const search = req.nextUrl.search || "";
    const targetUrl = `${baseUrl}/${subpath.replace(/^\//, "")}${search}`;

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    const auth = req.headers.get("authorization") || req.headers.get("Authorization");
    if (auth) {
      headers["Authorization"] = auth;
    }
    const apiKey = req.headers.get("x-api-key") || req.headers.get("X-API-Key");
    if (apiKey) {
      headers["X-API-Key"] = apiKey;
    }

    const upstream = await fetch(targetUrl, {
      method: req.method,
      headers,
      body: req.method !== "GET" && req.method !== "HEAD" ? bodyText : undefined,
      cache: "no-store",
    });

    const text = await upstream.text();
    let jsonBody: unknown;
    try {
      jsonBody = text ? JSON.parse(text) : {};
    } catch {
      jsonBody = { raw: text };
    }

    return NextResponse.json(jsonBody, { status: upstream.status });
  } catch (err) {
    return NextResponse.json(
      {
        error: "LocalStack API Gateway unreachable",
        message: err instanceof Error ? err.message : String(err),
      },
      { status: 503 }
    );
  }
}

export async function GET(
  req: NextRequest,
  { params }: { params: { slug: string[] } }
) {
  const subpath = (params.slug || []).join("/");
  return forwardToLocalStack(req, subpath);
}

export async function POST(
  req: NextRequest,
  { params }: { params: { slug: string[] } }
) {
  const subpath = (params.slug || []).join("/");
  const bodyText = await req.text();
  return forwardToLocalStack(req, subpath, bodyText);
}
