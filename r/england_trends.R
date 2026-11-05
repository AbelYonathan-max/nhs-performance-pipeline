# Independent check and trend analysis in R.
#
# Reads the Parquet files the Python pipeline publishes (data/out, or the
# `data` branch), and:
#   1. rebuilds the England monthly series from the trust-level facts and
#      checks they match the Python figures to within rounding;
#   2. fits a linear trend with month-of-year seasonality to each rate and
#      reports the yearly change with a 95% confidence interval;
#   3. compares that trend with the change needed to meet each national target
#      (gap to target), with a 95% prediction interval at the target date;
#   4. draws one chart per rate.
#
# Usage: Rscript r/england_trends.R [input folder] [output folder]
# Packages: nanoparquet (reads Parquet without Arrow), ggplot2.

suppressPackageStartupMessages({
  library(nanoparquet)
  library(ggplot2)
})

args <- commandArgs(trailingOnly = TRUE)
in_dir <- if (length(args) >= 1) args[1] else "data/out"
out_dir <- if (length(args) >= 2) args[2] else "data/analysis"
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

read <- function(name) as.data.frame(read_parquet(file.path(in_dir, paste0(name, ".parquet"))))
month_date <- function(key) as.Date(sprintf("%d-%02d-01", key %/% 100, key %% 100))

ae <- read("fact_ae")
rtt <- read("fact_rtt_england")
fc <- read("fact_forecast")
targets <- read("targets")

# ---- 1. Rebuild England series from the facts -------------------------------
# A&E 4-hour %: patients seen within 4 hours over attendances, leaving out trusts
# that did not report a 4-hour figure that month (reports_4hr = 0).
ae_eng <- aggregate(
  cbind(within_4hr, attendances, att_4hr = attendances * reports_4hr) ~ month_key,
  data = ae, FUN = sum
)
ae_eng$ae_4hr_pct <- ae_eng$within_4hr / ae_eng$att_4hr
names(ae_eng)[names(ae_eng) == "attendances"] <- "ae_attendances"

total_code <- if ("C_999" %in% rtt$treatment_function_code) "C_999" else stop("no RTT total row (C_999)")
rtt_eng <- rtt[rtt$treatment_function_code == total_code, c("month_key", "waiting_list", "within_18", "over_52")]
rtt_eng$rtt_18wk_pct <- rtt_eng$within_18 / rtt_eng$waiting_list
rtt_eng$rtt_waiting_list <- rtt_eng$waiting_list
rtt_eng$rtt_52wk_share <- rtt_eng$over_52 / rtt_eng$waiting_list

r_long <- rbind(
  data.frame(metric = "ae_4hr_pct", month_key = ae_eng$month_key, r_value = ae_eng$ae_4hr_pct),
  data.frame(metric = "ae_attendances", month_key = ae_eng$month_key, r_value = ae_eng$ae_attendances),
  data.frame(metric = "rtt_18wk_pct", month_key = rtt_eng$month_key, r_value = rtt_eng$rtt_18wk_pct),
  data.frame(metric = "rtt_waiting_list", month_key = rtt_eng$month_key, r_value = rtt_eng$rtt_waiting_list)
)
py <- fc[fc$provider_code == "ENG" & fc$kind == "actual", c("metric", "month_key", "value")]
names(py)[3] <- "python_value"
rec <- merge(r_long, py, by = c("metric", "month_key"), all = TRUE)
rec$abs_diff <- abs(rec$r_value - rec$python_value)
rec$rel_diff <- rec$abs_diff / abs(rec$python_value)

recon <- do.call(rbind, lapply(split(rec, rec$metric), function(d) data.frame(
  metric = d$metric[1],
  months_compared = sum(!is.na(d$abs_diff)),
  months_missing = sum(is.na(d$abs_diff)),
  max_rel_diff = max(d$rel_diff, na.rm = TRUE),
  passed = all(!is.na(d$abs_diff)) && max(d$rel_diff, na.rm = TRUE) < 1e-9
)))
write.csv(recon, file.path(out_dir, "r_reconciliation.csv"), row.names = FALSE)
print(recon, row.names = FALSE)
if (!all(recon$passed)) stop("R and Python England figures do not match: see r_reconciliation.csv")

# ---- 2 and 3. Trend, seasonality and gap to target --------------------------
rates <- list(
  ae_4hr_pct = list(label = "A&E seen within 4 hours", data = ae_eng[, c("month_key", "ae_4hr_pct")]),
  rtt_18wk_pct = list(label = "RTT waiting under 18 weeks", data = rtt_eng[, c("month_key", "rtt_18wk_pct")]),
  rtt_52wk_share = list(label = "RTT share waiting over 52 weeks", data = rtt_eng[, c("month_key", "rtt_52wk_share")])
)

trend_rows <- list()
for (key in names(rates)) {
  d <- rates[[key]]$data
  names(d) <- c("month_key", "y")
  d$date <- month_date(d$month_key)
  d$t <- as.numeric(d$date - min(d$date)) / 365.25         # years since first month
  d$moy <- factor(as.integer(format(d$date, "%m")), levels = 1:12)

  fit <- lm(y ~ t + moy, data = d)
  ci <- confint(fit, "t", level = 0.95)
  slope <- unname(coef(fit)["t"])
  seasonal_f <- anova(lm(y ~ t, data = d), fit)
  p_season <- seasonal_f[["Pr(>F)"]][2]

  last <- d[nrow(d), ]
  tg <- targets[targets$metric == key & targets$target_date > as.character(last$date), ]
  tg <- tg[order(tg$target_date), ][1, ]
  if (nrow(tg) == 0 || is.na(tg$value)) {
    target_value <- NA; target_date <- NA; needed <- NA; proj <- c(NA, NA, NA)
  } else {
    target_value <- tg$value
    target_date <- as.Date(tg$target_date)
    target_month <- as.Date(format(target_date, "%Y-%m-01"))
    years_left <- as.numeric(target_month - last$date) / 365.25
    needed <- (target_value - last$y) / years_left           # change a year needed from today
    new <- data.frame(t = as.numeric(target_month - min(d$date)) / 365.25,
                      moy = factor(as.integer(format(target_month, "%m")), levels = 1:12))
    proj <- predict(fit, new, interval = "prediction", level = 0.95)[1, ]
  }
  lower_is_better <- key == "rtt_52wk_share"
  on_track <- if (is.na(target_value)) NA else
    if (lower_is_better) proj[["upr"]] <= target_value else proj[["lwr"]] >= target_value

  trend_rows[[key]] <- data.frame(
    metric = key, label = rates[[key]]$label,
    first_month = min(d$month_key), last_month = last$month_key, latest_value = last$y,
    trend_per_year = slope, trend_ci95_low = ci[1], trend_ci95_high = ci[2],
    seasonality_p_value = p_season, r_squared = summary(fit)$r.squared,
    target_value = target_value, target_date = as.character(target_date),
    change_per_year_needed = needed,
    projected_at_target = proj[[1]], projected_pi95_low = proj[[2]], projected_pi95_high = proj[[3]],
    on_track_95 = on_track
  )

  # ---- 4. Chart -------------------------------------------------------------
  d$fitted <- fitted(fit)
  g <- ggplot(d, aes(date)) +
    geom_line(aes(y = y), colour = "#005EB8", linewidth = 0.9) +
    geom_line(aes(y = fitted), colour = "#768692", linetype = "dashed") +
    scale_y_continuous(labels = function(x) paste0(round(100 * x), "%")) +
    labs(title = rates[[key]]$label,
         subtitle = sprintf("Trend %+.1f points a year (95%% CI %+.1f to %+.1f), seasonality fitted",
                            100 * slope, 100 * ci[1], 100 * ci[2]),
         x = NULL, y = NULL, caption = "Source: NHS England monthly statistics. Dashed: linear trend + month of year.") +
    theme_minimal(base_size = 11)
  if (!is.na(target_value)) {
    g <- g + geom_hline(yintercept = target_value, colour = "#DA291C") +
      annotate("text", x = min(d$date), y = target_value, vjust = -0.5, hjust = 0, colour = "#DA291C",
               label = sprintf("Target %s%% by %s", round(100 * target_value), format(target_date, "%b %Y")))
  }
  ggsave(file.path(out_dir, paste0("trend_", key, ".png")), g, width = 8, height = 4.5, dpi = 150, bg = "white")
}

trend <- do.call(rbind, trend_rows)
write.csv(trend, file.path(out_dir, "r_trends.csv"), row.names = FALSE)
print(trend[, c("metric", "latest_value", "trend_per_year", "change_per_year_needed",
                "projected_at_target", "on_track_95")], row.names = FALSE)
cat("R analysis written to", out_dir, "\n")
