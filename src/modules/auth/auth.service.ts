import { AuthTokenType, type Role } from '@prisma/client';
import { env } from '../../config/env';
import { prisma } from '../../lib/prisma';
import { mailer } from '../../lib/mailer';
import { hashPassword, verifyPassword } from '../../lib/password';
import { badRequest, forbidden, HttpError, unauthorized } from '../../lib/errors';
import {
  daysFromNow,
  hashToken,
  hoursFromNow,
  minutesFromNow,
  randomToken,
  signAccessToken,
} from '../../lib/tokens';
import type { ChangePasswordInput, LoginInput, ResetPasswordInput, SignupInput } from './auth.schemas';

export interface Session {
  accessToken: string;
  refreshToken: string;
  refreshExpiresAt: Date;
  mustChangePassword: boolean;
}

const INVALID_LOGIN = new HttpError(401, 'INVALID_CREDENTIALS', 'Email or password is incorrect.');
const INVALID_LINK = badRequest('INVALID_OR_EXPIRED_TOKEN', 'This link is invalid or has expired.');
const SESSION_EXPIRED = 'Session expired. Please log in again.';

async function createSession(
  user: { id: number; role: Role; mustChangePassword: boolean },
  userAgent?: string,
): Promise<Session> {
  const refreshToken = randomToken();
  const refreshExpiresAt = daysFromNow(env.REFRESH_TOKEN_TTL_DAYS);
  await prisma.refreshToken.create({
    data: {
      userId: user.id,
      tokenHash: hashToken(refreshToken),
      userAgent: userAgent?.slice(0, 255),
      expiresAt: refreshExpiresAt,
    },
  });
  return {
    accessToken: signAccessToken(user.id, user.role),
    refreshToken,
    refreshExpiresAt,
    mustChangePassword: user.mustChangePassword,
  };
}

export async function revokeAllSessions(userId: number, exceptRefreshToken?: string): Promise<void> {
  await prisma.refreshToken.deleteMany({
    where: {
      userId,
      ...(exceptRefreshToken ? { NOT: { tokenHash: hashToken(exceptRefreshToken) } } : {}),
    },
  });
}

async function issueEmailToken(userId: number, type: AuthTokenType, expiresAt: Date): Promise<string> {
  const token = randomToken();
  await prisma.$transaction([
    prisma.authToken.deleteMany({ where: { userId, type, usedAt: null } }),
    prisma.authToken.create({ data: { userId, type, tokenHash: hashToken(token), expiresAt } }),
  ]);
  return token;
}

async function consumeEmailToken(token: string, type: AuthTokenType): Promise<number> {
  const now = new Date();
  const row = await prisma.authToken.findUnique({ where: { tokenHash: hashToken(token) } });
  if (!row || row.type !== type) throw INVALID_LINK;

  const { count } = await prisma.authToken.updateMany({
    where: { id: row.id, usedAt: null, expiresAt: { gt: now } },
    data: { usedAt: now },
  });
  if (count === 0) throw INVALID_LINK;
  return row.userId;
}

async function sendVerificationEmail(user: { id: number; name: string; email: string }): Promise<void> {
  const token = await issueEmailToken(user.id, AuthTokenType.verify_email, hoursFromNow(env.VERIFY_EMAIL_TTL_HOURS));
  const link = `${env.APP_URL}/verify-email?token=${encodeURIComponent(token)}`;
  await mailer.send({
    to: user.email,
    subject: 'Verify your email address',
    text:
      `Hello ${user.name},\n\n` +
      `Please verify your email address by opening this link:\n${link}\n\n` +
      `The link expires in ${env.VERIFY_EMAIL_TTL_HOURS} hours. If you did not sign up, ignore this email.`,
  });
}

// Same response whether or not the email is taken, so signup can't be used to discover accounts.
export async function signup(input: SignupInput): Promise<void> {
  const existing = await prisma.user.findUnique({ where: { email: input.email } });

  if (existing) {
    if (existing.isActive && !existing.emailVerifiedAt) await sendVerificationEmail(existing);
    return;
  }

  const user = await prisma.user.create({
    data: {
      name: input.name,
      email: input.email,
      passwordHash: await hashPassword(input.password),
      role: 'employee',
    },
  });
  await sendVerificationEmail(user);
}

export async function verifyEmail(token: string): Promise<void> {
  const userId = await consumeEmailToken(token, AuthTokenType.verify_email);
  const user = await prisma.user.findUnique({ where: { id: userId } });
  if (!user || !user.isActive) throw INVALID_LINK;
  if (!user.emailVerifiedAt) {
    await prisma.user.update({ where: { id: userId }, data: { emailVerifiedAt: new Date() } });
  }
}

export async function resendVerification(email: string): Promise<void> {
  const user = await prisma.user.findUnique({ where: { email } });
  if (user && user.isActive && !user.emailVerifiedAt) await sendVerificationEmail(user);
}

export async function login(input: LoginInput, userAgent?: string): Promise<Session> {
  const user = await prisma.user.findUnique({ where: { email: input.email } });
  const ok = await verifyPassword(input.password, user?.passwordHash);

  if (!user || !ok || !user.isActive) throw INVALID_LOGIN;
  if (!user.emailVerifiedAt) {
    throw forbidden('EMAIL_NOT_VERIFIED', 'Please verify your email address before logging in.');
  }
  return createSession(user, userAgent);
}

// Refresh tokens are single use: each refresh replaces the token with a new one.
export async function refresh(refreshToken: string | undefined, userAgent?: string): Promise<Session> {
  if (!refreshToken) throw unauthorized(SESSION_EXPIRED);

  const row = await prisma.refreshToken.findUnique({
    where: { tokenHash: hashToken(refreshToken) },
    include: { user: true },
  });
  if (!row) throw unauthorized(SESSION_EXPIRED);

  await prisma.refreshToken.delete({ where: { id: row.id } }).catch(() => undefined);

  if (row.expiresAt <= new Date() || !row.user.isActive) throw unauthorized(SESSION_EXPIRED);
  return createSession(row.user, userAgent);
}

export async function logout(refreshToken: string | undefined): Promise<void> {
  if (!refreshToken) return;
  await prisma.refreshToken.deleteMany({ where: { tokenHash: hashToken(refreshToken) } });
}

export async function changePassword(
  userId: number,
  input: ChangePasswordInput,
  currentRefreshToken?: string,
): Promise<void> {
  const user = await prisma.user.findUnique({ where: { id: userId } });
  if (!user) throw unauthorized();

  if (!(await verifyPassword(input.currentPassword, user.passwordHash))) {
    throw badRequest('WRONG_PASSWORD', 'Your current password is incorrect.');
  }

  await prisma.user.update({
    where: { id: userId },
    data: { passwordHash: await hashPassword(input.newPassword), mustChangePassword: false },
  });
  await revokeAllSessions(userId, currentRefreshToken);
}

export async function forgotPassword(email: string): Promise<void> {
  const user = await prisma.user.findUnique({ where: { email } });
  if (!user || !user.isActive) return;

  const token = await issueEmailToken(
    user.id,
    AuthTokenType.reset_password,
    minutesFromNow(env.RESET_PASSWORD_TTL_MINUTES),
  );
  const link = `${env.APP_URL}/reset-password?token=${encodeURIComponent(token)}`;
  await mailer.send({
    to: user.email,
    subject: 'Reset your password',
    text:
      `Hello ${user.name},\n\n` +
      `Someone asked to reset the password of your account. To choose a new password, open:\n${link}\n\n` +
      `The link works once and expires in ${env.RESET_PASSWORD_TTL_MINUTES} minutes. ` +
      `If you did not ask for this, ignore this email.`,
  });
}

export async function isResetTokenValid(token: string): Promise<boolean> {
  const row = await prisma.authToken.findUnique({
    where: { tokenHash: hashToken(token) },
    include: { user: { select: { isActive: true } } },
  });
  return Boolean(
    row &&
      row.type === AuthTokenType.reset_password &&
      !row.usedAt &&
      row.expiresAt > new Date() &&
      row.user.isActive,
  );
}

export async function resetPassword(input: ResetPasswordInput): Promise<void> {
  const userId = await consumeEmailToken(input.token, AuthTokenType.reset_password);
  const user = await prisma.user.findUnique({ where: { id: userId } });
  if (!user || !user.isActive) throw INVALID_LINK;

  await prisma.user.update({
    where: { id: userId },
    data: {
      passwordHash: await hashPassword(input.newPassword),
      mustChangePassword: false,
      emailVerifiedAt: user.emailVerifiedAt ?? new Date(),
    },
  });
  await revokeAllSessions(userId);
}
