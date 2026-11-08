import type { NextFunction, Request, Response } from 'express';
import type { ZodTypeAny, z } from 'zod';
import { badRequest } from '../lib/errors';

export function validateBody<S extends ZodTypeAny>(schema: S) {
  return (req: Request, _res: Response, next: NextFunction) => {
    const result = schema.safeParse(req.body ?? {});
    if (!result.success) {
      const fields = result.error.issues.map((i) => ({ field: i.path.join('.'), message: i.message }));
      return next(badRequest('VALIDATION_ERROR', 'Some fields are invalid.', fields));
    }
    req.body = result.data as z.infer<S>;
    next();
  };
}
