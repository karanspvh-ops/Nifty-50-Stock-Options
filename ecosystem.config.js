/**
 * ecosystem.config.js — PM2 daemon configuration.
 *
 * Commands:
 *   pm2 start ecosystem.config.js       ← start daemon
 *   pm2 stop trading-engine             ← stop
 *   pm2 restart trading-engine          ← restart
 *   pm2 logs trading-engine             ← live logs
 *   pm2 save && pm2 startup             ← survive PC reboot
 */

module.exports = {
  apps: [
    {
      name        : "trading-engine",
      script      : "python",
      args        : "-m uvicorn backend.main:app --host 0.0.0.0 --port 8000",
      cwd         : "C:\\Users\\karan\\OneDrive\\Desktop\\Project\\Algo Strategies\\Nifty 50 Stock Options",
      interpreter : "none",
      watch       : false,
      autorestart : true,
      max_restarts: 999,
      restart_delay: 2000,
      env: {
        PYTHONPATH: "C:\\Users\\karan\\OneDrive\\Desktop\\Project\\Algo Strategies\\Nifty 50 Stock Options"
      },
      log_file    : "./logs/trading-engine.log",
      out_file    : "./logs/trading-engine-out.log",
      error_file  : "./logs/trading-engine-err.log",
      time        : true,
    },
    {
      name        : "trading-frontend",
      script      : "cmd",
      args        : "/c npm run preview -- --port 3000 --host",
      cwd         : "C:\\Users\\karan\\OneDrive\\Desktop\\Project\\Algo Strategies\\Nifty 50 Stock Options\\frontend",
      interpreter : "none",
      watch       : false,
      autorestart : true,
      max_restarts: 999,
      restart_delay: 2000,
      log_file    : "../logs/frontend.log",
      out_file    : "../logs/frontend-out.log",
      error_file  : "../logs/frontend-err.log",
      time        : true,
    }
  ]
};
