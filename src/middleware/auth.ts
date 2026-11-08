import type { NextFunction, Request, Response } from 'express';
import type { Role } from '@prisma/client';
import { prisma } from '../lib/prisma';
import { verifyAccessToken } from '../lib/tokens';
import { forbidden, unauthorized } from '../lib/errors';

export interface AuthUser {
  id: number;
  role: Role;
  mustChangePassword: boolean;
}

declare global {
  namespace Express {
    interface Request {
      user?: AuthUser;
    }
  }
}

interface AuthOptions {
  allowPendingPasswordChange?: boolean;
}

// The user is read from the database on every request, so deactivation
// and role changes apply immediately instead of when the token expires.
export function requireAuth(options: AuthOptions = {}) {
  return async (req: Request, _res: Response, next: NextFunction) => {
    const header = req.headers.authorization ?? '';
    const token = header.startsWith('Bearer ') ? header.slice(7) : '';
    const payload = token ? verifyAccessToken(token) : null;
    if (!payload) return next(unauthorized());

    const user = await prisma.user.findUnique({
      where: { id: Number(payload.sub) },
      select: { id: true, role: true, isActive: true, mustChangePassword: true },
    });
    if (!user || !user.isActive) return next(unauthorized());

    if (user.mustChangePassword && !options.allowPendingPasswordChange) {
      return next(forbidden('PASSWORD_CHANGE_REQUIRED', 'You must change your password first.'));
    }

    req.user = { id: user.id, role: user.role, mustChangePassword: user.mustChangePassword };
    next();
  };
}

export function requireRole(...roles: Role[]) {
  return (req: Request, _res: Response, next: NextFunction) => {
    if (!req.user) return next(unauthorized());
    if (!roles.includes(req.user.role)) return next(forbidden());
    next();
  };
}
