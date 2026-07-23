import pandas as pd
import matplotlib.pyplot as plt
import os

# Set matplotlib fonts for Traditional Chinese
plt.rcParams['font.sans-serif'] = ['Microsoft JhengHei']  
plt.rcParams['axes.unicode_minus'] = False

def calc_kw_from_kwh(df, kwh_col_name):
    """
    Convert cumulative kWh to interval average kW.
    Interval is 15 minutes (0.25 hours), so kW = delta_kWh * 4.
    """
    # Calculate the difference between consecutive rows
    kw_series = df[kwh_col_name].diff() * 4
    # For the first element, assume the cumulative kWh is just from the first 15 mins
    kw_series.iloc[0] = df[kwh_col_name].iloc[0] * 4
    return kw_series

def plot_system_validation():
    base_path = r".\data\sample"
    
    print("📊 Loading data from three simulation stages...")
    try:
        # Load Battery Status Data
        df_base_bess = pd.read_csv(os.path.join(base_path, "Baseline_bess_status.csv"))
        df_pso_bess = pd.read_csv(os.path.join(base_path, "Pso_bess_status.csv"))
        df_island_bess = pd.read_csv(os.path.join(base_path, "Island_bess_status.csv"))
        
        # Load Meter Data (for Grid Power)
        df_base_meter = pd.read_csv(os.path.join(base_path, "Baseline_All_meter.csv"))
        df_pso_meter = pd.read_csv(os.path.join(base_path, "Pso_All_meter.csv"))
        df_island_meter = pd.read_csv(os.path.join(base_path, "Island_All_meter.csv"))
        
    except FileNotFoundError as e:
        print(f"❌ File not found! Please run main_hems.py first.\nDetails: {e}")
        return

    # Extract time labels
    time_labels = df_base_bess['Time'].tolist()
    x_ticks = range(len(time_labels))

    # Calculate Grid kW from cumulative kWh for all three modes
    meter_col = "總電錶T_當前累積功率(kWh)"
    grid_kw_base = calc_kw_from_kwh(df_base_meter, meter_col)
    grid_kw_pso = calc_kw_from_kwh(df_pso_meter, meter_col)
    grid_kw_island = calc_kw_from_kwh(df_island_meter, meter_col)

    # Create a 3-panel figure
    fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=True)

    # ==========================================
    # Plot 1: SOC (%) Variation
    # ==========================================
    axes[0].plot(df_base_bess['soc'], label='Baseline', linewidth=2.5, alpha=0.8)
    axes[0].plot(df_pso_bess['soc'], label='PSO Optimized', linewidth=2.5, linestyle='--')
    axes[0].plot(df_island_bess['soc'], label='Island Mode', linewidth=2.5, linestyle=':', color='red')
    axes[0].set_title('🔋 Battery State of Charge (SOC %)', fontsize=14, fontweight='bold')
    axes[0].set_ylabel('SOC (%)', fontsize=12)
    axes[0].axhline(y=20, color='gray', linestyle='-.', alpha=0.7, label='20% Protection Limit')
    axes[0].axhline(y=100, color='gray', linestyle='-.', alpha=0.7)
    axes[0].legend(loc='upper right')
    axes[0].grid(True, alpha=0.3)

    # ==========================================
    # Plot 2: Battery Charge/Discharge Power (kW)
    # ==========================================
    axes[1].plot(df_base_bess['battery_power_kw'], label='Baseline', linewidth=2.5, alpha=0.8)
    axes[1].plot(df_pso_bess['battery_power_kw'], label='PSO Optimized', linewidth=2.5, linestyle='--')
    axes[1].plot(df_island_bess['battery_power_kw'], label='Island Mode', linewidth=3, linestyle=':', color='red')
    axes[1].set_title('⚡ Battery Active Power (kW)', fontsize=14, fontweight='bold')
    axes[1].set_ylabel('kW (>0 Discharging / <0 Charging)', fontsize=12)
    axes[1].axhline(y=0, color='black', linewidth=1.5)
    axes[1].legend(loc='upper right')
    axes[1].grid(True, alpha=0.3)

    # ==========================================
    # Plot 3: Grid Power (PCC Demand in kW)
    # ==========================================
    axes[2].plot(grid_kw_base, label='Baseline Demand', linewidth=2.5, alpha=0.8)
    axes[2].plot(grid_kw_pso, label='PSO Shaved Demand', linewidth=2.5, linestyle='--')
    axes[2].plot(grid_kw_island, label='Island Demand (Grid Cutoff)', linewidth=3, linestyle=':', color='red')
    
    # Fill the area under the PSO curve to visualize energy
    axes[2].fill_between(x_ticks, 0, grid_kw_pso, color='orange', alpha=0.1)
    
    axes[2].set_title('🏢 Point of Common Coupling: Total Grid Demand (kW)', fontsize=14, fontweight='bold')
    axes[2].set_ylabel('Purchased Power (kW)', fontsize=12)
    axes[2].axhline(y=0, color='black', linewidth=1.5)
    
    # Set X-axis labels on the bottom plot only
    axes[2].set_xticks(x_ticks[::8])
    axes[2].set_xticklabels(time_labels[::8], rotation=45)
    axes[2].legend(loc='upper right')
    axes[2].grid(True, alpha=0.3)

    plt.tight_layout()
    print("✅ Rendering complete! Please check the visualization window.")
    plt.show()

if __name__ == "__main__":
    plot_system_validation()