import { Router } from 'express';
import { requireAuth } from '../../middleware/auth';
import { getProfile } from './users.service';

export const usersRouter = Router();

usersRouter.get('/me', requireAuth({ allowPendingPasswordChange: true }), async (req, res) => {
  res.json(await getProfile(req.user!.id));
});
