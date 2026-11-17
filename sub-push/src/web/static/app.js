new Vue({
  el: "#app",
  data: {
    activeTab: 0,
    tabs: [],
    items: []
  },
  methods: {
    getItemLink(item) {
      const prefix = "https://www.jpgoodbuy.com/purchase/";
      if (item.source_site === "paypay") {
        return prefix + encodeURIComponent("https://paypayfleamarket.yahoo.co.jp/item/" + item.id);
      }
      return prefix + encodeURIComponent("https://jp.mercari.com/item/" + item.id);
    },
    async switchTab(index, file) {
      this.activeTab = index;
      await this.loadData(file);
    },
    async loadData(file) {
      const res = await fetch(file);
      const data = await res.json();
      this.items = (data.Data && data.Data.data) ? data.Data.data : [];
    }
  },
  async mounted() {
    const filesRes = await fetch("file.json");
    const files = await filesRes.json();
    this.tabs = files.map(f => ({ name: f.replace(".json", ""), file: f }));
    if (this.tabs.length > 0) {
      await this.loadData(this.tabs[0].file);
    }
  }
});

