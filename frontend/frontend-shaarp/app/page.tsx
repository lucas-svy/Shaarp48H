import Image from "next/image";
export default function Home() {
  return (
    <div className="flex h-screen bg-gray-100 font-sans">

      {/* Volet latéral gauche */}
      <div className="w-[300px] bg-gray-800 text-white flex flex-col p-4">
        <h2 className="text-xl font-bold mb-6">Menu</h2>
        <a href="#" className="py-2 px-15 rounded hover:bg-gray-700 text-center">Nouveau chat</a>

      </div>

      {/* Contenu principal */}
      <div className="flex-1 flex flex-col items-center justify-center relative">
        <button className="ml-2 flex items-center justify-center">
          <img src="arrow.png"></img>
        </button>
        <div className="relative top-[-400px] w-full h-[150px] bg-white"></div>
        <a href="https://exemple.com/a-propos" target="_blank" rel="noopener noreferrer"
          className="relative top-[-475px] w-[200px] h-[40px] bg-blue-500 text-white rounded-full flex items-center justify-center hover:bg-blue-700"
>
  À propos de nous
</a>

        <button className="relative top-[300px] w-[900px] h-[50px] bg-blue-300 border rounded-full text-gray flex items-center justify-start px-4">
          Tappez un url
        </button>
      </div>
    </div>
  );
}