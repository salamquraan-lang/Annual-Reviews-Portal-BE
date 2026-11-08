import { Router, type Request, type Response } from 'express';
import { env } from '../../config/env';
import { requireAuth } from '../../middleware/auth';
import { authLimiter } from '../../middleware/rateLimit';
import { validateBody } from '../../middleware/validate';
import {
  changePasswordSchema,
  emailOnlySchema,
  loginSchema,
  resetPasswordSchema,
  signupSchema,
  tokenSchema,
} from './auth.schemas';
import * as auth from './auth.service';

export const REFRESH_COOKIE = 'refresh_token';
const COOKIE_PATH = '/api/auth';

function setRefreshCookie(res: Response, session: auth.Session) {
  res.cookie(REFRESH_COOKIE, session.refreshToken, {
    httpOnly: true,
    secure: env.NODE_ENV === 'production',
    sameSite: 'strict',
    path: COOKIE_PATH,
    expires: session.refreshExpiresAt,
  });
}

function clearRefreshCookie(res: Response) {
  res.clearCookie(REFRESH_COOKIE, { path: COOKIE_PATH });
}

const readRefreshCookie = (req: Request): string | undefined => req.cookies?.[REFRESH_COOKIE];

function sessionBody(session: auth.Session) {
  return { accessToken: session.accessToken, mustChangePassword: session.mustChangePassword };
}

export const authRouter = Router();

authRouter.post('/signup', authLimiter, validateBody(signupSchema), async (req, res) => {
  await auth.signup(req.body);
  res.status(202).json({
    message: 'If this email can be used, we have sent a verification link to it. Check your inbox.',
  });
});

authRouter.post('/verify-email', authLimiter, validateBody(tokenSchema), async (req, res) => {
  await auth.verifyEmail(req.body.token);
  res.json({ message: 'Your email is verified. You can log in now.' });
});

authRouter.post('/resend-verification', authLimiter, validateBody(emailOnlySchema), async (req, res) => {
  await auth.resendVerification(req.body.email);
  res.status(202).json({ message: 'If this account needs verification, we have sent a new link.' });
});

authRouter.post('/login', authLimiter, validateBody(loginSchema), async (req, res) => {
  const session = await auth.login(req.body, req.get('user-agent'));
  setRefreshCookie(res, session);
  res.json(sessionBody(session));
});

authRouter.post('/refresh', authLimiter, async (req, res) => {
  try {
    const session = await auth.refresh(readRefreshCookie(req), req.get('user-agent'));
    setRefreshCookie(res, session);
    res.json(sessionBody(session));
  } catch (err) {
    clearRefreshCookie(res);
    throw err;
  }
});

authRouter.post('/logout', async (req, res) => {
  await auth.logout(readRefreshCookie(req));
  clearRefreshCookie(res);
  res.status(204).end();
});

authRouter.post('/logout-all', requireAuth({ allowPendingPasswordChange: true }), async (req, res) => {
  await auth.revokeAllSessions(req.user!.id);
  clearRefreshCookie(res);
  res.status(204).end();
});

authRouter.post(
  '/change-password',
  authLimiter,
  requireAuth({ allowPendingPasswordChange: true }),
  validateBody(changePasswordSchema),
  async (req, res) => {
    await auth.changePassword(req.user!.id, req.body, readRefreshCookie(req));
    res.json({ message: 'Your password has been changed. Your other devices were logged out.' });
  },
);

authRouter.post('/forgot-password', authLimiter, validateBody(emailOnlySchema), async (req, res) => {
  await auth.forgotPassword(req.body.email);
  res.status(202).json({ message: 'If this email is registered, we have sent a link to reset the password.' });
});

authRouter.get('/reset-password/validate', authLimiter, async (req, res) => {
  const token = typeof req.query.token === 'string' ? req.query.token : '';
  res.json({ valid: token ? await auth.isResetTokenValid(token) : false });
});

authRouter.post('/reset-password', authLimiter, validateBody(resetPasswordSchema), async (req, res) => {
  await auth.resetPassword(req.body);
  clearRefreshCookie(res);
  res.json({ message: 'Your password has been reset. You can log in with the new password.' });
});
