import rateLimit from 'express-rate-limit';
import { env } from '../config/env';

const windowMs = env.RATE_LIMIT_WINDOW_MINUTES * 60_000;
const message = { error: { code: 'TOO_MANY_REQUESTS', message: 'Too many requests. Try again later.' } };

export const apiLimiter = rateLimit({
  windowMs,
  limit: env.RATE_LIMIT_MAX,
  standardHeaders: 'draft-7',
  legacyHeaders: false,
  message,
  skip: () => env.NODE_ENV === 'test',
});

// Only failed requests count, so users who log in correctly are never blocked.
export const authLimiter = rateLimit({
  windowMs,
  limit: env.AUTH_RATE_LIMIT_MAX,
  standardHeaders: 'draft-7',
  legacyHeaders: false,
  skipSuccessfulRequests: true,
  message,
  skip: () => env.NODE_ENV === 'test',
});
