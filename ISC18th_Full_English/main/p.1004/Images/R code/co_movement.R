# Load required libraries
library(ggplot2)
library(dplyr)
library(forcats)
library(scales)

# Create data frame with the co-movement index values
companies <- c("Dana", "Saman", "Sina", "Dey", "Novin", "Pasargad", 
               "Asia", "Iran", "Kowsar", "Mellat", "Razi", "Karafarin", 
               "Mihan", "Parsian", "Alborz", "Moalem")

index_values <- c(0.727272727, 0.727272727, 0.727272727, 0.636363636, 
                  0.636363636, 0.636363636, 0.545454545, 0.545454545, 
                  0.545454545, 0.545454545, 0.545454545, 0.454545455, 
                  0.454545455, 0.454545455, 0.272727273, 0.181818182)

# Create dataframe
df <- data.frame(
  Company = companies,
  Index = index_values
)

# Add category labels based on the paper's classification
df <- df %>%
  mutate(
    Category = case_when(
      Index >= 0.7 ~ "Relatively Strong (0.73)",
      Index >= 0.6 ~ "Noticeable (0.64)",
      Index >= 0.5 ~ "Random (0.54)",
      Index >= 0.4 ~ "Random (0.45)",
      Index >= 0.2 ~ "Relatively Strong Inverse (0.27)",
      TRUE ~ "Strong Inverse (0.18)"
    ),
    # Order companies by index value (descending)
    Company = factor(Company, levels = Company[order(Index, decreasing = TRUE)])
  )

# Define color palette for categories
category_colors <- c(
  "Relatively Strong (0.73)" = "#2E7D32",        # Dark green
  "Noticeable (0.64)" = "#66BB6A",                # Medium green
  "Random (0.54)" = "#FFA726",                    # Orange
  "Random (0.45)" = "#FFB74D",                    # Light orange
  "Relatively Strong Inverse (0.27)" = "#EF5350", # Light red
  "Strong Inverse (0.18)" = "#C62828"             # Dark red
)

# Option 1: Horizontal bar chart (recommended for company names)
p1 <- ggplot(df, aes(x = Index, y = Company, fill = Category)) +
  geom_col(width = 0.7) +
  geom_text(aes(label = sprintf("%.2f", Index)), 
            hjust = -0.3, size = 3.5) +
  scale_fill_manual(values = category_colors) +
  scale_x_continuous(
    limits = c(0, 0.85),
    breaks = seq(0, 0.8, 0.1),
    expand = c(0, 0)
  ) +
  labs(
    title = "Co-movement Index for Iranian Insurance Companies (2012-2023)",
    subtitle = "Measuring directional alignment between solvency ratio changes and policy sales",
    x = "Co-movement Index Value",
    y = "Insurance Company",
    fill = "Interpretation Category",
    caption = "Source: Authors' calculations based on Central Insurance of Iran data"
  ) +
  theme_minimal() +
  theme(
    plot.title = element_text(size = 14, face = "bold", hjust = 0.5),
    plot.subtitle = element_text(size = 11, hjust = 0.5, color = "gray40"),
    axis.title = element_text(size = 11),
    axis.text.y = element_text(size = 10),
    legend.position = "bottom",
    legend.title = element_text(size = 10, face = "bold"),
    legend.text = element_text(size = 9),
    panel.grid.major.y = element_blank(),
    panel.grid.minor.x = element_blank()
  ) +
  guides(fill = guide_legend(nrow = 2, byrow = TRUE))

# Display the plot
print(p1)

# Save the plot (optional)
ggsave("comovement_index_plot.png", p1, width = 10, height = 8, dpi = 300)

# Option 2: Vertical bar chart with angled labels
p2 <- ggplot(df, aes(x = Company, y = Index, fill = Category)) +
  geom_col(width = 0.7) +
  geom_text(aes(label = sprintf("%.2f", Index)), 
            vjust = -0.5, size = 3.5) +
  scale_fill_manual(values = category_colors) +
  scale_y_continuous(
    limits = c(0, 0.85),
    breaks = seq(0, 0.8, 0.1),
    expand = c(0, 0)
  ) +
  labs(
    title = "Co-movement Index for Iranian Insurance Companies (2012-2023)",
    subtitle = "Measuring directional alignment between solvency ratio changes and policy sales",
    x = "Insurance Company",
    y = "Co-movement Index Value",
    fill = "Interpretation Category",
    caption = "Source: Authors' calculations based on Central Insurance of Iran data"
  ) +
  theme_minimal() +
  theme(
    plot.title = element_text(size = 14, face = "bold", hjust = 0.5),
    plot.subtitle = element_text(size = 11, hjust = 0.5, color = "gray40"),
    axis.title = element_text(size = 11),
    axis.text.x = element_text(angle = 45, hjust = 1, size = 9),
    axis.text.y = element_text(size = 10),
    legend.position = "bottom",
    legend.title = element_text(size = 10, face = "bold"),
    legend.text = element_text(size = 9),
    panel.grid.major.x = element_blank(),
    panel.grid.minor.y = element_blank()
  ) +
  guides(fill = guide_legend(nrow = 2, byrow = TRUE))

# Option 3: Lollipop chart (alternative visualization)
p3 <- ggplot(df, aes(x = Index, y = Company, color = Category)) +
  geom_segment(aes(x = 0, xend = Index, y = Company, yend = Company), 
               linewidth = 1, color = "gray70") +
  geom_point(size = 4) +
  scale_color_manual(values = category_colors) +
  scale_x_continuous(
    limits = c(0, 0.85),
    breaks = seq(0, 0.8, 0.1),
    expand = c(0, 0)
  ) +
  labs(
    title = "Co-movement Index for Iranian Insurance Companies (2012-2023)",
    subtitle = "Lollipop chart showing directional alignment between solvency and sales",
    x = "Co-movement Index Value",
    y = "Insurance Company",
    color = "Interpretation Category",
    caption = "Source: Authors' calculations based on Central Insurance of Iran data"
  ) +
  theme_minimal() +
  theme(
    plot.title = element_text(size = 14, face = "bold", hjust = 0.5),
    plot.subtitle = element_text(size = 11, hjust = 0.5, color = "gray40"),
    axis.title = element_text(size = 11),
    axis.text.y = element_text(size = 10),
    legend.position = "bottom",
    legend.title = element_text(size = 10, face = "bold"),
    legend.text = element_text(size = 9),
    panel.grid.major.y = element_blank(),
    panel.grid.minor.x = element_blank()
  ) +
  guides(color = guide_legend(nrow = 2, byrow = TRUE))

# Create a summary table of categories
category_summary <- df %>%
  group_by(Category) %>%
  summarise(
    Companies = paste(Company, collapse = ", "),
    Count = n(),
    Min_Index = min(Index),
    Max_Index = max(Index)
  ) %>%
  arrange(desc(Max_Index))

# Print summary table
cat("\n=== Category Summary ===\n")
print(category_summary)

# Calculate summary statistics
cat("\n=== Summary Statistics ===\n")
cat("Mean Index:", mean(df$Index), "\n")
cat("Median Index:", median(df$Index), "\n")
cat("Standard Deviation:", sd(df$Index), "\n")
cat("Min Index:", min(df$Index), "-", df$Company[which.min(df$Index)], "\n")
cat("Max Index:", max(df$Index), "-", 
    paste(df$Company[which(df$Index == max(df$Index))], collapse = ", "), "\n")

# Create a simple base R plot as alternative (if ggplot2 is not available)
if(FALSE) {  # Set to TRUE if you want to use base R graphics
  # Sort data for plotting
  df_base <- df[order(df$Index),]
  
  # Create horizontal bar plot
  par(mar = c(5, 8, 4, 2))  # Increase left margin for company names
  barplot(df_base$Index, 
          names.arg = df_base$Company,
          horiz = TRUE,
          las = 1,
          col = colorRampPalette(c("red", "yellow", "green"))(16),
          main = "Co-movement Index for Iranian Insurance Companies",
          xlab = "Co-movement Index Value",
          xlim = c(0, 0.8))
  grid()
  abline(v = 0.5, col = "gray50", lty = 2)
  text(df_base$Index + 0.03, seq_along(df_base$Index) - 0.1, 
       labels = sprintf("%.2f", df_base$Index), cex = 0.8)
}